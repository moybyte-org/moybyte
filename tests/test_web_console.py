"""Tests for the web console (#22, cutoff #2: draw-command streaming).

The web is now a NEW CANVAS BACKEND, not a pixel viewer. The shared host
`Workstation` draws through an injected canvas; the web server swaps in a
`CommandCanvas` (tools/command_canvas.py) that RECORDS each draw call into a
per-frame, JSON-serializable command list, ships it via GET /frame, and a JS
replayer redraws it in the browser. These tests cover both halves:

  * THE FAITHFULNESS CROSS-CHECK (the key test): render the launcher and a running
    cart TWO ways -- (a) the normal rasterizing host `Canvas` (ws.canvas.buf), and
    (b) a Python reference replayer that executes the `CommandCanvas` command list
    onto a fresh host `Canvas` -- and assert the two buffers are PIXEL-IDENTICAL.
    This proves the command stream captures everything the renderer needs (the same
    way the map() agent validated its C kernel against the Python rasterizer).

  * THE SERVER (localhost only, ephemeral port, no external network):
      GET  /          -> the HTML page (a <canvas> + the replayer JS).
      GET  /assets    -> JSON palette + petme128 font + the open cart's sheet.
      GET  /ws        -> the persistent WebSocket live channel: the server PUSHES a
                         stepped frame ({"cmds","cart","gen","audio"}) per tick and
                         the browser pushes {"events":[...]} input UP -- one socket,
                         the SAME transport the device speaks (no HTTP poll fallback).
"""

import base64
import http.client
import json
import os
import socket
import threading
import time

import pytest

from runtime import host_app
from runtime import web_view
from runtime.canvas import Canvas
from runtime.web_view import CommandCanvas, ServedState, replay_to_canvas
from tools import web_console

WIDTH, HEIGHT = host_app.WIDTH, host_app.HEIGHT


# ---------------------------------------------------------------------------
# Faithfulness cross-check: CommandCanvas stream replays pixel-identically.
# ---------------------------------------------------------------------------
#
# The check must compare the SAME frame two ways. Driving two independent consoles
# would diverge on non-deterministic per-frame state (the wall clock in the status
# strip, the FPS readout, time-based wallpaper/cart animation), so instead we run
# ONE console through a TeeCanvas: every draw call is BOTH rasterized into a real
# host Canvas AND recorded as a command, in a single pass over identical state.
# Replaying the recorded commands onto a fresh Canvas must then reproduce the
# rasterized buffer byte-for-byte -- proving the stream captures everything the
# renderer needs (the same approach the map() agent used to validate its C kernel).


class TeeCanvas:
    """A test canvas that forwards every draw call to a real rasterizing host Canvas
    AND to a CommandCanvas recorder, so both see identical calls from one frame. It
    exposes the full Canvas surface the console/carts use; `buf` aliases the
    rasterized buffer and `take_commands()` the recorder's list."""

    def __init__(self, width=WIDTH, height=HEIGHT):
        self.w = width
        self.h = height
        self.raster = Canvas(width, height)
        self.rec = CommandCanvas(width, height)

    @property
    def buf(self):
        return self.raster.buf

    def take_commands(self):
        return self.rec.take_commands()

    def cls(self, c=0):
        self.raster.cls(c); self.rec.cls(c)

    def pix(self, x, y, c=None):
        if c is None:
            return self.raster.pix(x, y)        # a read goes to the real buffer
        self.raster.pix(x, y, c); self.rec.pix(x, y, c)

    def line(self, x0, y0, x1, y1, c):
        self.raster.line(x0, y0, x1, y1, c); self.rec.line(x0, y0, x1, y1, c)

    def rect(self, x, y, w, h, c):
        self.raster.rect(x, y, w, h, c); self.rec.rect(x, y, w, h, c)

    def rectb(self, x, y, w, h, c):
        self.raster.rectb(x, y, w, h, c); self.rec.rectb(x, y, w, h, c)

    def circ(self, cx, cy, r, c):
        self.raster.circ(cx, cy, r, c); self.rec.circ(cx, cy, r, c)

    def circb(self, cx, cy, r, c):
        self.raster.circb(cx, cy, r, c); self.rec.circb(cx, cy, r, c)

    def spr(self, img, x, y, scale=1, flip=0):
        self.raster.spr(img, x, y, scale, flip); self.rec.spr(img, x, y, scale, flip)

    def spr_tile(self, sheet, tile, x, y, colorkey=-1, scale=1, flip=0):
        # Fold-1 auto-batch (#63): the raster canvas queues + coalesces; the recorder
        # emits one per-spr command. Both are flushed/complete by frame end.
        self.raster.spr_tile(sheet, tile, x, y, colorkey, scale, flip)
        self.rec.spr_tile(sheet, tile, x, y, colorkey, scale, flip)

    def flush_batch(self):
        self.raster.flush_batch()

    def spr_batch(self, sheet, items, colorkey=-1, scale=1):
        self.raster.spr_batch(sheet, items, colorkey, scale)
        self.rec.spr_batch(sheet, items, colorkey, scale)

    def map(self, tilemap, sheet, mx=0, my=0, w=None, h=None,
            sx=0, sy=0, colorkey=-1, scale=1):
        self.raster.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)
        self.rec.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)

    # Draw state (#11): forward to BOTH so the recorded stream replays identically.
    def reset_state(self):
        self.raster.reset_state(); self.rec.reset_state()

    def camera(self, x=0, y=0):
        self.rec.camera(x, y)
        return self.raster.camera(x, y)

    def clip(self, x=None, y=None, w=None, h=None):
        self.raster.clip(x, y, w, h); self.rec.clip(x, y, w, h)

    def pal(self, c0=None, c1=None):
        self.raster.pal(c0, c1); self.rec.pal(c0, c1)

    def palt(self, c=None, on=None):
        self.raster.palt(c, on); self.rec.palt(c, on)

    def print(self, s, x, y, c, scale=1):
        self.raster.print(s, x, y, c, scale); self.rec.print(s, x, y, c, scale)

    # Offscreen layers (#54 scroll window / #43 cached top bar): ONE recorded-layer
    # mechanism. The recorder mints a RecordingLayer (its OWN real rasterizing Canvas +
    # its OWN recorded command stream), so a draw into the layer rasterizes AND records;
    # the copy forwards to BOTH the rasterizer (pixels, off the layer's real Canvas) and
    # the recorder (the tiny blit_layer reference op). The deflayer that ships the
    # layer's stream is prepended by rec.take_commands() (ship-once).
    def new_layer(self, w, h):
        return self.rec.new_layer(w, h)

    def blit_window_from(self, layer, cam_x=0, cam_y=0):
        # The shared RecordingLayer stores its real backing Canvas as `_c`.
        self.raster.blit_window_from(layer._c, cam_x, cam_y)
        self.rec.blit_window_from(layer, cam_x, cam_y)

    def blit_strip(self, layer, dst_x=0, dst_y=0):
        self.raster.blit_strip(layer._c, dst_x, dst_y)
        self.rec.blit_strip(layer, dst_x, dst_y)

    def to_rgb888(self):
        return self.raster.to_rgb888()


def _build_tee(save_dir):
    """A Workstation over a seeded carts dir, drawing through a TeeCanvas (single
    source of truth for both the raster + recorded views). Returns (ws, driver, tee)."""
    ws = host_app.build_workstation(save_dir)
    tee = TeeCanvas(WIDTH, HEIGHT)
    ws.canvas = tee                       # the decided swap: reassign ws.canvas
    return ws, host_app.ConsoleDriver(ws), tee


def _served(tee):
    """The command list the browser actually RECEIVES for this frame: route the recorder's
    raw commands through the SHARED ServedState (the exact serve path the device + host run),
    which prepends a ["deflayer", ...] the FIRST time a served frame references a layer
    (ship-once, #54/#43). A persistent per-tee ServedState makes the dedup span frames."""
    st = getattr(tee, "_served_state", None)
    if st is None:
        st = ServedState(tee.rec._rec)
        tee._served_state = st
    return st.served_frame(tee.take_commands())


def _assert_frame_identical(ws, drv, tee, dt=1.0 / 30, label=""):
    """Step the console one frame, then assert the rasterized buffer equals a fresh
    replay of that same frame's SERVED command list, byte for byte.

    The replay keeps a PERSISTENT off-screen-layer cache on the tee across frames (the
    browser's per-id layer canvases): with ship-once layers (#54/#43) a deflayer rides
    only the frame its gen changed, so a later frame's blit_layer must resolve against
    the layer the browser already replayed -- exactly as the browser page does.

    The cross-check needs a RECORDED frame to compare, so force the redraw: a static
    screen (the default Moy Night wallpaper doesn't animate) legitimately records
    nothing under the #44 dirty gate, which is streaming behavior, not a parity
    subject."""
    ws._dirty = True
    drv.frame(dt)
    raster = bytes(tee.buf)
    cv = Canvas(WIDTH, HEIGHT)
    layers = getattr(tee, "_replay_layers", None)
    if layers is None:
        layers = {}
        tee._replay_layers = layers
    replay_to_canvas(_served(tee), cv, layers)
    replayed = bytes(cv.buf)
    assert len(raster) == len(replayed) == WIDTH * HEIGHT
    if raster != replayed:
        diff = sum(1 for a, b in zip(raster, replayed) if a != b)
        first = next(i for i, (a, b) in enumerate(zip(raster, replayed)) if a != b)
        pytest.fail("%s: command replay differs from rasterizer in %d/%d px "
                    "(first at index %d: raster=%d replay=%d)"
                    % (label, diff, len(raster), first,
                       raster[first], replayed[first]))


def _real_tile_center(ws):
    """Center of the first REAL cart's shelf card (slot 0 is the pinned Make
    pseudo tile) -- computed from the live scrolled grid, since the shelf's
    card geometry is layout-derived rather than a frozen constant."""
    i = next(i for i, it in enumerate(ws.launcher.items) if it.get("path"))
    x, y, w, h = ws.launcher.tile_rect(i)
    return x + w // 2, y + h // 2


def _open_tile0(ws, drv):
    """Tap-open the first real cart (launcher -> desktop) on a TeeCanvas-driven
    console. A launcher tap RUNS the cart (spec shell_ux_v1.md, the locked model);
    this cross-check needs the running cart's draw stream."""
    tx, ty = _real_tile_center(ws)
    drv.touch(tx, ty)
    drv.frame(1.0 / 30)
    drv.touch_up()
    for _ in range(8):
        drv.frame(1.0 / 30)
        if ws.screen == "desktop":
            return
    raise AssertionError("the first real cart should open into the desktop")


def test_crosscheck_launcher_is_pixel_identical(tmp_path):
    """The DESKTOP/launcher home -- wallpaper + cart icon grid (spr) + status strip +
    dock + text -- recorded as commands and replayed must equal the rasterized frame.
    Exercises cls/rect/rectb/spr/print on the real launcher."""
    ws, drv, tee = _build_tee(str(tmp_path / "carts"))
    assert ws.screen == "launcher"
    for n in range(3):
        _assert_frame_identical(ws, drv, tee, label="launcher#%d" % n)


def test_crosscheck_running_cart_is_pixel_identical(tmp_path):
    """Open a cart and run it: its per-frame draw (cls/spr/print/rect/circ/line/map,
    whatever it calls) recorded + replayed must match the rasterized cart frame."""
    ws, drv, tee = _build_tee(str(tmp_path / "carts"))
    assert ws.launcher.items, "system carts should be seeded"
    _open_tile0(ws, drv)
    # Run a stretch of frames (motion + draw variety) and check each.
    for n in range(12):
        _assert_frame_identical(ws, drv, tee, label="cart#%d" % n)


def test_crosscheck_named_cart_with_map(tmp_path):
    """A cart that uses map()/spr (battle_city has a tilemap) round-trips identically
    -- proves map() expansion to per-cell spr commands replays pixel-perfect."""
    cart_path = os.path.join(host_app.ROOT, "system_carts", "battle_city.moy")
    if not os.path.isdir(cart_path):
        pytest.skip("battle_city.moy not present")
    ws, drv, tee = _build_tee(str(tmp_path / "carts"))
    for i, c in enumerate(ws.launcher.items):
        if os.path.basename(c.get("path") or "") == "battle_city.moy":
            ws.launcher.sel = i
            break
    else:
        pytest.skip("battle_city.moy not in the seeded store")
    ws.open()
    assert ws.screen == "desktop"
    for n in range(10):
        _assert_frame_identical(ws, drv, tee, label="map#%d" % n)


def test_crosscheck_scene_cart_and_placement_editor(tmp_path):
    """The #85 web-view knock-on, verified: scenes need NO asset threading over
    the draw-command transport -- scene() is pure data consumed server-side at
    _init (only its ordinary spr/rect draw verbs reach the wire, unlike map(),
    whose replay needs the browser's cached tilemap). A seeded scene consumer
    (Hop Quest) AND the Stage 2 placement editor tab itself both round-trip
    pixel-identical."""
    cart_path = os.path.join(host_app.ROOT, "system_carts", "platformer.moy")
    if not os.path.isdir(os.path.join(cart_path, "scenes")):
        pytest.skip("platformer.moy scene assets not present")
    ws, drv, tee = _build_tee(str(tmp_path / "carts"))
    for i, c in enumerate(ws.launcher.items):
        if os.path.basename(c.get("path") or "") == "platformer.moy":
            ws.launcher.sel = i
            break
    else:
        pytest.skip("platformer.moy not in the seeded store")
    ws.open()
    assert ws.screen == "desktop"
    assert len(ws.scenes.scene()) > 0          # the cart really loaded its scene
    for n in range(6):
        _assert_frame_identical(ws, drv, tee, label="scenecart#%d" % n)
    # The placement editor's own surface (world view + palette + props row).
    ws._open_scene()
    assert ws.menu_view == "scene"
    for n in range(3):
        _assert_frame_identical(ws, drv, tee, label="sceneedit#%d" % n)


def test_take_commands_clears_per_frame(tmp_path):
    """take_commands() returns the frame's list AND resets it, so commands never
    accumulate across frames (the wire stays a few calls/frame)."""
    cv = CommandCanvas(WIDTH, HEIGHT)
    cv.cls(1)
    cv.rect(0, 0, 10, 10, 2)
    first = cv.take_commands()
    assert first and first[0] == ["cls", 1]
    assert cv.take_commands() == []          # cleared
    cv.print("hi", 4, 4, 7)
    second = cv.take_commands()
    assert len(second) == 1 and second[0][0] == "print"


def test_launcher_frame_clears_the_buffer_each_redraw(tmp_path):
    """Regression: the launcher's command stream MUST contain a cls so the browser's
    retained index buffer is wiped before each redraw. Otherwise the chrome that has
    no opaque background -- the selection outline + cart labels -- ghosts as the
    selection moves (two yellow boxes, doubled text). The cause was the wallpaper cart
    being compiled against the original canvas during build_workstation, BEFORE
    WebConsole swaps in the recording canvas, so its cls()/backdrop never reached the
    stream (the device clears its framebuffer regardless, so it only showed on the web).
    WebConsole now rebinds the wallpaper to the recording canvas."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30)
    assert console.ws.screen == "launcher"
    console.ws.select_wallpaper("ocean", persist=False)   # a LIVE wallpaper (animates)
    for _ in range(3):                       # let the live wallpaper settle
        cmds, _, _ = console.step_frame()
    assert "cls" in [c[0] for c in cmds], "launcher frame never clears -> browser ghosts"
    # The bug only surfaced after a selection move; assert it still clears then.
    console.apply_events([{"type": "hold", "name": "right", "down": True}])
    cmds, _, _ = console.step_frame()
    console.apply_events([{"type": "hold", "name": "right", "down": False}])
    assert "cls" in [c[0] for c in cmds], "post-nav frame never clears -> browser ghosts"


def test_step_frame_partitions_into_wm_surfaces(tmp_path):
    """End-to-end host wiring (Stage 9): the console frame loop marks each wm.draw_stack()
    surface via canvas.begin_surface, so WebConsole.step_frame ships the browser ONE stream
    PER window-manager surface (bar / app-content / player-viewport) -- the browser becomes a
    second WM backend that composites them. Assert the launcher frame splits into >= 2 tagged
    surfaces whose composite reproduces the FLAT served frame pixel-for-pixel (they are a slice
    of it), and that a running cart yields the 'desktop' player-viewport surface."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30)
    assert console.ws.screen == "launcher"
    cmds, _, _ = console.step_frame()
    surfaces = console._last_surfaces
    assert surfaces is not None and len(surfaces) >= 2, "the launcher splits into WM surfaces"
    assert "cursor" in [s["id"] for s in surfaces], "the cursor is its own WM surface"
    # Compositing the surfaces IN ORDER reproduces the flat served frame pixel-for-pixel (both
    # replay the same command sequence: the "_defs" prefix + the sliced surfaces == the flat cmds).
    flat_cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(cmds, flat_cv)
    surf_cv = Canvas(WIDTH, HEIGHT)
    web_view.replay_surfaces_to_canvas(surfaces, surf_cv)
    assert bytes(surf_cv.buf) == bytes(flat_cv.buf), "surface composite must match the flat frame"
    assert len(set(surf_cv.buf)) > 1, "the composited launcher must not be blank"
    # Open a cart -> the running cart is the 'desktop' player-viewport surface (a
    # launcher tap RUNS the cart, spec shell_ux_v1.md).
    tx, ty = _real_tile_center(console.ws)
    console.apply_events([{"type": "down", "x": tx, "y": ty}])
    console.step_frame()
    console.apply_events([{"type": "up"}])
    for _ in range(8):
        console.step_frame()
        if console.ws.screen == "desktop":
            break
    assert console.ws.screen == "desktop"
    console.step_frame()
    assert "desktop" in [s["id"] for s in console._last_surfaces], (
        "the running cart is the 'desktop' player-viewport surface")


def test_streaming_contract_static_skips_live_streams(tmp_path):
    """The streaming contract (the VPN-bandwidth fix): a STATIC screen streams
    NOTHING (step_frame -> None; the browser retains its last frame and the
    keyframe rides the /assets fetch -- see test_idle_static_screen_pushes_nothing),
    while an ANIMATING screen (a live wallpaper, a running cart) streams a COMPLETE
    frame every poll (has a cls, so the browser's retained buffer never ghosts)."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30)
    # Static: the Moy Night default doesn't animate -> nothing on the wire.
    console.ws.select_wallpaper("moy_night", persist=False)
    console.step_frame()                         # the /assets-armed keyframe
    # The shelf's cover art builds ONE per frame (the T-Deck hitch budget), so
    # the first paints keep the gate dirty until every visible cover landed.
    for _ in range(160):
        cmds, _, _ = console.step_frame()
        if cmds is None:
            break
    assert cmds is None, "a static screen must stream nothing (idle = free)"
    cmds, _, _ = console.step_frame()
    assert cmds is None, "... and stay idle"
    # Animating: the ocean wallpaper redraws -> every poll is a full frame.
    console.ws.select_wallpaper("ocean", persist=False)
    console.step_frame()
    cmds, _, _ = console.step_frame()
    assert cmds, "an animating screen must stream every poll"
    assert "cls" in [c[0] for c in cmds], "animated frame must be a full redraw (has cls)"


def test_fake_audio_take_pcm_hands_off_the_rendered_block():
    """Browser audio (#22): FakeAudio.tick already rendered the mixed PCM each frame
    and discarded it; now it keeps the block and take_pcm() hands it off (and clears,
    so a stale block is never re-streamed). This is the whole server side -- the
    browser plays these finished samples, no second synth."""
    from runtime.audio import AudioBank, AudioEngine
    au = host_app.FakeAudio(AudioEngine(AudioBank.default()))
    au.sfx(0)                        # trigger sfx 0 (the coin blip)
    au.tick(1.0 / 30)                # render one frame's PCM
    pcm = au.take_pcm()
    assert pcm and any(pcm), "a playing sound must yield non-silent PCM"
    assert au.take_pcm() == b"", "take_pcm must clear -> no stale re-send"


def test_step_frame_carries_audio_and_assets_advertise_the_rate(tmp_path):
    """/frame returns (cmds, cart, audio_b64) and /assets carries the engine's PCM
    sample rate so the browser schedules playback correctly. audio_b64 is '' on the
    silent launcher (nothing playing), which the browser simply skips."""
    from runtime.audio import AudioEngine
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30)
    out = console.step_frame()
    assert len(out) == 3 and isinstance(out[2], str)     # (cmds, cart, audio_b64)
    assert console.assets()["audio_rate"] == AudioEngine().rate


def test_command_canvas_api_matches_canvas():
    """CommandCanvas must expose the same public draw surface as the host Canvas, so
    it's a drop-in ws.canvas. Assert every drawing method the console/carts call
    exists and is callable."""
    cv = CommandCanvas(WIDTH, HEIGHT)
    for name in ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
                 "spr", "map", "print", "to_rgb888"):
        assert callable(getattr(cv, name)), name
    assert cv.w == WIDTH and cv.h == HEIGHT
    # pix as a read returns 0 (a command stream has no buffer to read back).
    assert cv.pix(5, 5) == 0


# ---------------------------------------------------------------------------
# Off-screen LAYERS (#54 scroll + #43 cached top bar): ONE recorded-layer mechanism.
# The recorder mints a RecordingLayer (real Canvas + its own recorded stream); the
# copy emits a tiny blit_layer; the deflayer ships the stream once. The cross-check
# replays the SERVED command stream (deflayer prepended) into a fresh Canvas via the
# persistent layer cache and asserts it equals the rasterizer pixel-for-pixel.
# ---------------------------------------------------------------------------


def test_scroll_draw_layer_replays_pixel_identical_at_camera_offset():
    """A wide scroll layer pre-rendered ONCE, then window-copied at a camera offset
    (draw_layer -> blit_window_from) replays byte-identically -- and the layer's stream
    ships as ONE deflayer, then a tiny blit_layer per frame (no pixels on the wire)."""
    tee = TeeCanvas(WIDTH, HEIGHT)
    # Build a layer WIDER than the screen and paint a recognisable world into it once.
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(1)
    lay.rect(0, HEIGHT - 40, WIDTH * 2, 40, 3)       # ground band
    for gx in range(0, WIDTH * 2, 60):
        lay.circ(gx + 10, 40, 8, 7)                  # clouds across the world
        lay.rect(gx + 30, HEIGHT - 60, 6, 20, 4)     # posts
    # Frame: window-copy the visible slice at a camera offset + draw an actor on top.
    tee.cls(0)
    tee.blit_window_from(lay, 137, 0)                # mid-world camera
    tee.rect(150, 100, 12, 22, 8)                    # the runner, on top
    cmds = _served(tee)
    # The served stream carries the layer ONCE: exactly one deflayer + one blit_layer.
    assert [c[0] for c in cmds].count("deflayer") == 1
    assert [c[0] for c in cmds].count("blit_layer") == 1
    bl = next(c for c in cmds if c[0] == "blit_layer")
    assert bl[1:] == [lay.id, 137, 0], "a windowed blit_layer (no 'full' marker)"
    # Replay the served stream onto a fresh Canvas and assert pixel-identical.
    cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(cmds, cv, {})
    assert bytes(cv.buf) == bytes(tee.buf), "scroll draw_layer replay must match the rasterizer"
    assert len(set(cv.buf)) > 1, "the scroll frame must not be a flat (black) screen"


def test_cached_top_bar_blit_strip_replays_pixel_identical():
    """The cached top-bar strip (blit_strip) uses the SAME layer mechanism as the scroll
    layer: rendered once into a short strip layer, then full-copied at a fixed offset.
    The served stream carries ONE deflayer + a 'full' blit_layer, and replays exactly."""
    tee = TeeCanvas(WIDTH, HEIGHT)
    strip = tee.new_layer(WIDTH, 18)                 # an 18px top-bar strip
    strip.rect(0, 0, WIDTH, 18, 0)                   # black bar
    strip.rect(0, 17, WIDTH, 1, 5)                   # shelf edge
    strip.print("12:34", 280, 3, 6)                  # clock
    strip.rect(8, 2, 14, 14, 7)                      # an icon block
    # Frame: clear, then stamp the cached bar at the top.
    tee.cls(2)
    tee.rect(40, 60, 100, 80, 9)                     # some content under the bar
    tee.blit_strip(strip, 0, 0)
    cmds = _served(tee)
    assert [c[0] for c in cmds].count("deflayer") == 1
    bl = next(c for c in cmds if c[0] == "blit_layer")
    assert bl == ["blit_layer", strip.id, 0, 0, "full"], "a full blit_layer (the cached bar)"
    cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(cmds, cv, {})
    assert bytes(cv.buf) == bytes(tee.buf), "cached-bar blit_strip replay must match the rasterizer"


def test_layer_ships_once_then_reference_only_across_frames():
    """A layer pre-rendered once ships its deflayer on the FIRST frame that references
    it, then every later frame is a tiny blit_layer (no deflayer) -- the ship-once
    bandwidth win. The cross-check keeps a persistent layer cache so the later frames
    still replay correctly (the browser keeps its off-screen layer canvases)."""
    tee = TeeCanvas(WIDTH, HEIGHT)
    lay = tee.new_layer(WIDTH * 2, HEIGHT)
    lay.cls(4)
    lay.rect(0, 100, WIDTH * 2, 40, 8)
    layers = {}                                      # the persistent browser-side cache
    # Frame 1: the deflayer is PREPENDED (like the serve-time defspr) + cls + blit_layer.
    tee.cls(0); tee.blit_window_from(lay, 0, 0)
    f1 = _served(tee)
    assert [c[0] for c in f1] == ["deflayer", "cls", "blit_layer"]
    cv1 = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(f1, cv1, layers)
    assert bytes(cv1.buf) == bytes(tee.buf)
    # Frame 2: the SAME layer, no redraw -> NO deflayer (already shipped), just blit_layer.
    tee.cls(0); tee.blit_window_from(lay, 64, 0)
    f2 = _served(tee)
    assert [c[0] for c in f2] == ["cls", "blit_layer"], "a shipped layer is not re-sent"
    cv2 = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(f2, cv2, layers)                # resolves against the cached layer
    assert bytes(cv2.buf) == bytes(tee.buf)


def test_layer_reships_deflayer_on_redraw():
    """When a layer is REDRAWN (the cached bar repainted on a clock tick / theme change),
    its gen bumps and the next served frame re-ships the deflayer with the fresh stream."""
    tee = TeeCanvas(WIDTH, HEIGHT)
    strip = tee.new_layer(WIDTH, 18)
    strip.rect(0, 0, WIDTH, 18, 0)
    strip.print("12:34", 280, 3, 6)
    tee.cls(0); tee.blit_strip(strip, 0, 0)
    f1 = _served(tee)
    g1 = next(c for c in f1 if c[0] == "deflayer")
    assert "12:34" in str(g1), "the first deflayer carries the original bar stream"
    # No redraw -> no deflayer next frame.
    tee.cls(0); tee.blit_strip(strip, 0, 0)
    assert not any(c[0] == "deflayer" for c in _served(tee))
    # REDRAW the strip (a new clock minute): re-render its body -> gen bumps -> re-ship.
    strip.rect(0, 0, WIDTH, 18, 0)
    strip.print("12:35", 280, 3, 6)
    tee.cls(0); tee.blit_strip(strip, 0, 0)
    f3 = _served(tee)
    g3 = next((c for c in f3 if c[0] == "deflayer"), None)
    assert g3 is not None and "12:35" in str(g3), "a redrawn layer re-ships its fresh stream"


# ---------------------------------------------------------------------------
# Paint-image assets (#63 Fold 3): a paint image baked into a layer ships COMPACTLY.
# ---------------------------------------------------------------------------


def test_nameless_paint_image_layer_ships_one_compact_img_fallback():
    """FALLBACK path: a paint image with NO asset name (built ad-hoc, not via image('name'),
    so it has no /assets entry to reference) baked into a layer with spr(bg, 0, 0) must record
    as ONE compact inline ["img", ...] (base64 indices) inside the layer's deflayer, NOT a
    self-contained spr with a 76,800-int pix array (the ~1MB stream that wouldn't load in the
    browser). The served deflayer replays pixel-identically to the rasterizer with NO /assets."""
    from runtime.canvas import Image

    tee = TeeCanvas(WIDTH, HEIGHT)
    # A full-screen paint image (indices), tagged _paint but NAMELESS -> inline img fallback.
    idx = bytearray(WIDTH * HEIGHT)
    for i in range(len(idx)):
        idx[i] = (i * 7) % 63
    bg = Image(WIDTH, HEIGHT, idx, -1)
    bg._paint = True                      # no _name -> the inline fallback, not imgref

    lay = tee.new_layer(WIDTH, HEIGHT)
    lay.spr(bg, 0, 0)                     # ONE bake into the layer (the #63 path)
    tee.cls(0)
    tee.blit_window_from(lay, 0, 0)

    served = _served(tee)
    deflayers = [c for c in served if c[0] == "deflayer"]
    assert len(deflayers) == 1, "the paint bg layer ships exactly one deflayer"
    lcmds = deflayers[0][4]
    imgs = [c for c in lcmds if c[0] == "img"]
    assert len(imgs) == 1, "the nameless paint bg is ONE inline img command"
    assert not any(c[0] == "imgref" for c in lcmds), "a nameless image has no name to reference"
    # No fat self-contained spr (9-field, pix array) rode along.
    assert not any(c[0] == "spr" and len(c) > 7 for c in lcmds), "no fat inline spr"
    img = imgs[0]
    assert img[3] == WIDTH and img[4] == HEIGHT and isinstance(img[5], str)
    # The compact wire is ~base64 of the raw indices (~4/3 * w*h), a bounded one-time
    # blob -- and MUCH smaller than 76,800 JSON ints (~a few hundred KB to ~1MB).
    assert len(img[5]) < WIDTH * HEIGHT * 2, "img blob must be compact (base64 indices)"

    # Replay the served frame (no /assets) -> pixel-identical to the rasterizer.
    cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(served, cv, {})
    assert bytes(cv.buf) == bytes(tee.buf), "paint-image layer replay must match raster"


def test_named_paint_image_layer_ships_imgref_not_pixels():
    """The NORMAL path: a NAMED paint image (image('bg') tags img._name) baked into a layer
    records a tiny ["imgref", x, y, name] inside the deflayer -- NO pixels on the wire. The
    /assets images dict carries the bytes, and replaying the deflayer against those /assets
    images is pixel-identical to the rasterizer."""
    from runtime.canvas import Image

    tee = TeeCanvas(WIDTH, HEIGHT)
    idx = bytearray(WIDTH * HEIGHT)
    for i in range(len(idx)):
        idx[i] = (i * 11) % 63
    bg = Image(WIDTH, HEIGHT, idx, -1)
    bg._paint = True
    bg._name = "bg"                       # image('bg') tags this -> imgref, pixels via /assets

    lay = tee.new_layer(WIDTH, HEIGHT)
    lay.spr(bg, 0, 0)
    tee.cls(0)
    tee.blit_window_from(lay, 0, 0)

    served = _served(tee)
    deflayers = [c for c in served if c[0] == "deflayer"]
    assert len(deflayers) == 1
    lcmds = deflayers[0][4]
    imgrefs = [c for c in lcmds if c[0] == "imgref"]
    assert imgrefs == [["imgref", 0, 0, "bg"]], "the named bg is ONE imgref, by name"
    assert not any(c[0] == "img" for c in lcmds), "a named image ships no inline pixels"
    assert _longest_str(served) < 1000, "no fat base64 rides the deflayer"

    # The /assets images dict carries the pixels; replay against it -> pixel-identical.
    from runtime import palette as _pal
    assets = web_view.assets_payload(
        WIDTH, HEIGHT, _pal.MOY64, None, None, "T", 8000, {"bg": (WIDTH, HEIGHT, bytes(idx))})
    cv = Canvas(WIDTH, HEIGHT)
    replay_to_canvas(served, cv, {}, assets)
    assert bytes(cv.buf) == bytes(tee.buf), "imgref layer replay must match raster"


def test_device_atlas_imgref_frees_the_defspr_budget_so_sprites_ship():
    """The #63 Fold 4 starvation fix, on the DEVICE atlas path (self_contained=False): a paint
    background baked into a layer now ships a TINY imgref deflayer, so the fat 110KB blob no
    longer eats a frame's defspr budget -- the cart's per-tile sprites (sakura's petals) get
    their defspr bitmaps. Drives the shared DrawRecorder + ServedState directly (the device
    recorder is grepped, not executed, in firmware tests)."""
    from runtime.canvas import Canvas as _C, Image
    from runtime.web_view import DrawRecorder, RecordingLayer

    rec = DrawRecorder(WIDTH, HEIGHT)         # self_contained stays False = the DEVICE atlas path
    rec.enabled = True
    st = ServedState(rec)

    bg = Image(WIDTH, HEIGHT, bytes(WIDTH * HEIGHT), -1)
    bg._paint = True
    bg._name = "bg"
    # 8 unique "petal" tiles (the device reuses one Image per tile -> stable id -> atlas dedup).
    petals = [Image(4, 4, bytes([(k + 1) % 63]) * 16, -1) for k in range(8)]

    lay = RecordingLayer(_C(WIDTH, HEIGHT), rec)   # sakura bakes bg into make_layer(...)
    lay.spr(bg, 0, 0)

    # Frame 0: the layer's deflayer ships (a tiny imgref); ServedState zeroes the defspr budget
    # for the frame it ships a deflayer, so the petals' defsprs ride LATER frames.
    rec.begin()
    rec.blit_layer_window(lay, 0, 0)               # draw_layer
    for k, p in enumerate(petals):
        rec.spr(p, k * 8, 0)
    rec.commit()
    f0 = st.served_frame(rec.frame())
    deflayers = [c for c in f0 if c[0] == "deflayer"]
    assert len(deflayers) == 1
    assert [c for c in deflayers[0][4] if c[0] == "imgref"] == [["imgref", 0, 0, "bg"]]
    assert _longest_str(f0) < 1000, "the deflayer carries a tiny imgref, not a 110KB blob"

    # Frame 1: no new deflayer -> the full defspr budget is free -> every petal bitmap ships.
    rec.begin()
    for k, p in enumerate(petals):
        rec.spr(p, k * 8, 0)
    rec.commit()
    f1 = st.served_frame(rec.frame())
    defsprs = [c for c in f1 if c[0] == "defspr"]
    assert len(defsprs) == len(petals), \
        "all petal defsprs ship once the fat blob no longer starves the budget (got %d)" % len(defsprs)


def _longest_str(node):
    """The length of the longest string ANYWHERE in a nested command list -- used to prove no
    fat base64 image blob rides the served stream / deflayer (the imgref pixels live in /assets)."""
    if isinstance(node, str):
        return len(node)
    if isinstance(node, (list, tuple)):
        return max((_longest_str(x) for x in node), default=0)
    return 0


def _sakura_assets(ws):
    """The /assets payload a browser fetches for the open cart -- palette + font + sheet +
    tilemap + the DECODED paint images (the Fold-4 path: image bytes ship here ONCE, not on the
    frame stream). Built exactly as tools/web_console.WebConsole.assets does (host provider)."""
    from runtime import palette as _pal
    decoded = {}
    for name, blob in (getattr(ws, "images", None) or {}).items():
        dec = host_app._decode_moyimg(blob)
        if dec is not None:
            decoded[name] = dec
    return web_view.assets_payload(WIDTH, HEIGHT, _pal.MOY64,
                                   getattr(ws, "sheet", None), getattr(ws, "tilemap", None),
                                   "Sakura", 8000, decoded or None)


def test_sakura_paint_bg_ships_via_assets_and_replays_pixel_identical(tmp_path):
    """End-to-end (#63 Fold 4 acceptance): the real sakura cart run through the console + web
    recorder ships its painted background by NAME -- a tiny ["imgref", ...] inside the deflayer,
    with the PIXELS in /assets (once, browser-cached) -- NOT the ~110KB base64 blob inline. So:
      * /assets carries the `bg` image (w/h/b64),
      * the served frames + deflayer carry NO large base64 (only imgref),
      * the petals' sprites still ship (no defspr starvation), and
      * every served frame replays pixel-identically to the rasterizer via imgref + /assets."""
    ws, drv, tee = _build_tee(str(tmp_path / "carts"))
    # Sakura is a WALLPAPER, so it leaves the launcher run-grid (spec shell_ux_v1.md); it
    # stays a real editable cart in the full store, so open it by reference (the same
    # _open_workspace + run that ws.open() does, just for a non-run-grid cart).
    sakura = next((c for c in ws._all_carts
                   if os.path.basename(c.get("path") or "") == "sakura.moy"), None)
    if sakura is None:
        pytest.skip("sakura.moy not in the seeded store")
    ws._open_workspace(sakura)
    ws.run(ws.project, ws.launcher_layer)
    assert ws.screen == "desktop" and ws.cart_error is None

    # /assets carries the decoded bg image (the pixels ship HERE, once per cart).
    assets = _sakura_assets(ws)
    assert assets["images"] and "bg" in assets["images"], "/assets must carry the bg image"
    bg = assets["images"]["bg"]
    assert bg["w"] == WIDTH and bg["h"] == HEIGHT and isinstance(bg["b64"], str)
    assert len(bg["b64"]) > 1000, "the bg image bytes really do live in /assets"

    tee._replay_layers = {}
    deflayer_frames = []
    for n in range(6):
        drv.frame(1.0 / 30)
        raster = bytes(tee.buf)
        served = _served(tee)
        # The stream references the paint image by NAME, never by pixels: no inline ["img", ...]
        # anywhere (top-level or inside a deflayer), and no fat base64 blob on the wire.
        def _walk(cmds):
            for c in cmds:
                yield c
                if c and c[0] == "deflayer":
                    for x in c[4]:
                        yield x
        assert not any(c[0] == "img" for c in _walk(served)), "no inline img blob"
        assert _longest_str(served) < 1000, \
            "no fat base64 in the served stream (frame %d): got %d" % (n, _longest_str(served))
        imgrefs = [c for c in _walk(served) if c[0] == "imgref"]
        if any(c[0] == "deflayer" for c in served):
            deflayer_frames.append(n)
            assert len(imgrefs) == 1 and imgrefs[0][3] == "bg", \
                "sakura's bg is ONE imgref, by name"
        # Replay the SERVED frame against the /assets images -> pixel-identical to the raster.
        cv = Canvas(WIDTH, HEIGHT)
        replay_to_canvas(served, cv, tee._replay_layers, assets)
        assert bytes(cv.buf) == raster, "sakura frame %d replay differs from raster" % n

    # The painted bg layer ships its deflayer ONCE (frame 0), not every frame.
    assert deflayer_frames == [0], "the paint bg must ship once, got %r" % deflayer_frames


# ---------------------------------------------------------------------------
# The server (localhost only, ephemeral port).
# ---------------------------------------------------------------------------


@pytest.fixture()
def server(tmp_path):
    """A web console serving an isolated carts dir on an ephemeral localhost port.
    Yields (host_console, host, port) and tears the server down afterward."""
    save_dir = str(tmp_path / "carts")
    console = web_console.WebConsole(save_dir, fps=30)
    srv = web_console.make_server(console, host="127.0.0.1", port=0)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield console, "127.0.0.1", port
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def _get(host, port, path):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read()
    finally:
        conn.close()


def _get_json(host, port, path):
    status, ctype, body = _get(host, port, path)
    return status, ctype, json.loads(body.decode("utf-8"))


def _mask_client_frame(payload, opcode=0x1, mask=b"\x21\x9a\x03\x7c"):
    """Build a MASKED client->server WebSocket frame (the shape a browser sends), so the test
    client feeds the host server real wire bytes. Mirrors RFC 6455 5.3 + the device test's
    helper: MASK bit set + 4-byte key, payload XOR mask[i%4], the size-appropriate length form."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    n = len(payload)
    b0 = 0x80 | (opcode & 0x0F)
    if n < 126:
        hdr = bytes((b0, 0x80 | n))
    elif n < 65536:
        hdr = bytes((b0, 0x80 | 126, (n >> 8) & 0xFF, n & 0xFF))
    else:
        hdr = bytes((b0, 0x80 | 127)) + bytes((n >> (8 * (7 - i))) & 0xFF for i in range(8))
    body = bytearray(payload)
    for i in range(n):
        body[i] ^= mask[i & 3]
    return hdr + mask + bytes(body)


class _WSClient:
    """A tiny raw-socket WebSocket client for the host server: do the RFC 6455 handshake against
    /ws (verifying the Sec-WebSocket-Accept via the SHARED web_view.ws_accept_key), then send
    masked text frames UP and read the server's UNMASKED text frames DOWN. Dependency-free (no
    `websockets` lib) so it always runs -- the host twin of the device's WS round-trip test."""

    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = ("GET /ws HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (host, key))
        self.s.sendall(req.encode("ascii"))
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.s.recv(4096)
            if not chunk:
                raise AssertionError("server closed during the WS handshake")
            resp += chunk
        head, _, rest = resp.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 101"), head
        assert web_view.ws_accept_key(key).encode("ascii") in head, "bad Sec-WebSocket-Accept"
        self.buf = rest                       # server bytes read past the 101 (first frame may glue)

    def _need(self, n):
        while len(self.buf) < n:
            self.s.settimeout(5)
            chunk = self.s.recv(4096)
            if not chunk:
                raise AssertionError("server closed before a full frame arrived")
            self.buf += chunk

    def recv_text(self):
        """Read + return ONE server->client text frame's payload (bytes). Server frames are
        unmasked; supports the 7/16/64-bit length forms."""
        self._need(2)
        ln = self.buf[1] & 0x7F
        masked = (self.buf[1] & 0x80) != 0
        off = 2
        if ln == 126:
            self._need(4)
            ln = (self.buf[2] << 8) | self.buf[3]
            off = 4
        elif ln == 127:
            self._need(10)
            ln = 0
            for i in range(8):
                ln = (ln << 8) | self.buf[2 + i]
            off = 10
        if masked:
            self._need(off + 4)
            off += 4
        self._need(off + ln)
        payload = self.buf[off:off + ln]
        self.buf = self.buf[off + ln:]
        return payload

    def send_events(self, events):
        self.s.sendall(_mask_client_frame(json.dumps({"events": events})))

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def test_index_serves_html_page(server):
    _console, host, port = server
    status, ctype, body = _get(host, port, "/")
    assert status == 200
    assert "text/html" in ctype
    text = body.decode("utf-8")
    # The page is the replayer thin client: a scaled <canvas>, fetches /assets over HTTP, then
    # opens the persistent WebSocket (/ws) live channel -- no HTTP poll endpoints anymore.
    assert "<canvas" in text
    assert "/assets" in text
    assert "/ws" in text and "WebSocket" in text
    assert "/frame" not in text and "/input" not in text


def test_browser_page_sends_neutral_pan_on_arrow_release():
    """Regression: arrows are a held pan velocity; release must send dx=0/dy=0."""
    text = web_view.PAGE_HTML
    assert "panWas=false" in text
    assert 'send({type:"pan",dx:0,dy:0})' in text


def test_browser_page_fills_large_viewports_without_integer_scale_cliff():
    text = web_view.PAGE_HTML
    assert "Math.floor(s)" not in text
    assert "Math.min(rw/W,rh/H)" in text
    assert "(hover:hover) and (pointer:fine)" in text


def test_soft_keyboard_toggle_and_hidden_input_are_served(server):
    """#42 Thread 2: the page must ship a touch-only ⌨ toggle (#kb) and a hidden real <input>
    (#kbin) to focus so a phone's on-screen keyboard opens. #kb lives in the bottom control
    bar's middle cluster (#mid, beside the burger) -- NEVER floating over the canvas, where it
    used to sit exactly on the console's OS status zone / context-X and eat its taps. #kbin
    must keep autocapitalize/autocorrect/autocomplete OFF (clean code typing); the whole bar
    (incl. #kb) hides on a hover-capable/fine-pointer device -- physical keyboard/mouse users
    never see it."""
    _console, host, port = server
    status, ctype, body = _get(host, port, "/")
    assert status == 200
    text = body.decode("utf-8")
    assert 'id=kb ' in text or "id=kb>" in text
    assert "id=kbin" in text
    assert 'autocapitalize=off' in text and 'autocomplete=off' in text
    assert 'autocorrect=off' in text and 'spellcheck=false' in text
    assert "#ctl{display:none}" in text
    # The toggle sits INSIDE the control bar's middle cluster, not pinned over the canvas.
    assert "<div id=mid><span class=b id=kb" in text
    assert "cvwrap" not in text and "position:absolute;top:6px" not in text


def test_touch_long_press_never_starts_text_selection():
    """Holding a control (hold-to-exit burger, d-pad, A/B) must never start the OS
    text-selection: iOS needs -webkit-user-select + -webkit-touch-callout (plain user-select
    isn't honored there) on the whole page for holds that drift off a button, and Android
    fires contextmenu on long-press even with pointerdown preventDefault'd. Touch-scoped:
    desktop text selection and right-click stay normal, and #kbin stays selectable (its
    caret is driven programmatically)."""
    text = web_view.PAGE_HTML
    assert "-webkit-user-select:none" in text and "-webkit-touch-callout:none" in text
    before_guard = text[:text.index("-webkit-user-select:none")]
    assert "(pointer:coarse)" in before_guard.rsplit("@media", 1)[1], \
        "the selection guard must be scoped to touch devices"
    assert "#kbin{-webkit-user-select:text;user-select:text}" in text
    assert 'document.addEventListener("contextmenu"' in text
    assert 't.closest("#ctl")' in text


def test_soft_keyboard_routes_typed_text_through_the_key_protocol():
    """The hidden input must feed the SAME {"type":"key"} wire the physical keydown handler
    uses (server-side this already fans out to type_char, incl. symbols like = [ ] { } < > % --
    any printable ASCII 0x20-0x7e), never a new event shape. Backspace is detected by the
    sentinel-value going empty, Enter by its own keydown listener (a single-line <input> never
    inserts a literal newline)."""
    text = web_view.PAGE_HTML
    assert "kbInp=document.getElementById(\"kbin\")" in text
    assert 'send({type:"key",code:8})' in text        # Backspace: sentinel deleted
    assert 'send({type:"key",code:13})' in text        # Enter (both the input diff and keydown paths)
    assert "c>=32&&c<=126" in text                     # printable ASCII incl. = [ ] { } < > %
    assert 'kbInp.addEventListener("keydown"' in text
    assert 'kbInp.addEventListener("input"' in text


def test_soft_keyboard_focus_scrolls_the_canvas_into_view():
    """Requirement: the canvas viewport stays visible while the on-screen keyboard is up (the
    keyboard eats the bottom of the viewport on a phone) -- focusing #kbin scrolls #cv into
    view, re-applied on every visualViewport resize (the keyboard opening/animating)."""
    text = web_view.PAGE_HTML
    assert "cv.scrollIntoView(" in text
    assert "window.visualViewport" in text
    assert 'kbInp.addEventListener("focus"' in text and "kbScroll" in text


def test_soft_keyboard_toggle_blurs_to_hide():
    """Tapping #kb again (while #kbin is focused) blurs it -- the phone's own teardown for
    dismissing its on-screen keyboard; no separate hide/show DOM state to manage."""
    text = web_view.PAGE_HTML
    assert "kbBtn.addEventListener(\"click\"" in text
    assert "kbInp.blur()" in text


def test_web_console_pan_zero_stops_arrow_velocity(tmp_path):
    """The host driver keeps pan as a held velocity until a neutral pan arrives."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30)
    console.apply_events([{"type": "pan", "dx": 1, "dy": 0}])
    assert console.driver._pan == (1, 0)
    console.step_frame()
    assert console.driver._pan == (1, 0)
    console.apply_events([{"type": "pan", "dx": 0, "dy": 0}])
    assert console.driver._pan == (0, 0)


def test_web_console_key_batch_delivers_every_char_in_order(tmp_path):
    """#42 Thread 2 regression: a phone soft keyboard swipe-typing/autocorrect-committing
    a word lands as ONE WebSocket batch of {"type":"key"} events. ConsoleDriver.type_char
    used to be last-wins (`self._typed = code`), so 'hello' typed only 'o'. The driver now
    QUEUES typed chars and frame() feeds ONE per frame into last_key (the console's
    one-key-per-frame contract), so every char reaches a text consumer, in order."""
    from runtime import moy_carts

    carts_dir = str(tmp_path / "carts")
    os.makedirs(carts_dir, exist_ok=True)
    # A text-mode cart that records every nonzero key() it sees, one frame at a time.
    moy_carts.create("Typer", carts_dir, type="app", src="""
typed = ""

def _update(dt):
    global typed
    textmode(True)
    k = key()
    if k:
        typed = typed + chr(k)

def _draw():
    cls(0)
""")
    console = web_console.WebConsole(carts_dir, fps=30)
    ws = console.ws
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == "Typer":
            ws.launcher.sel = i
            break
    ws.open()
    assert ws.screen == "desktop" and ws.cart_error is None
    console.step_frame()                       # _update ran textmode(True)
    assert ws.input.text_mode is True

    # One WS batch, five typed chars (incl. a repeat -- 'll' -- so ordering AND
    # completeness are both pinned; last-wins would leave just 'o').
    console.apply_events([{"type": "key", "code": ord(ch)} for ch in "hello"])
    for _ in range(5):
        console.step_frame()                   # one char drains per frame
    assert ws.ns["typed"] == "hello"
    console.step_frame()                       # the queue is spent -- no repeats
    assert ws.ns["typed"] == "hello"


def test_assets_returns_palette_font_and_sheet(server):
    _console, host, port = server
    status, ctype, obj = _get_json(host, port, "/assets")
    assert status == 200
    assert "application/json" in ctype
    assert obj["w"] == WIDTH and obj["h"] == HEIGHT
    # MOY64 palette: 64 [r,g,b] triples.
    assert len(obj["palette"]) == 64
    assert obj["palette"][7] == [0xFF, 0xF1, 0xE8]      # MOY64 index 7 = white
    # petme128 font: glyphs as 8 column-bytes each, ASCII 0x20..0x7f.
    font = obj["font"]
    assert font["first"] == 0x20 and font["w"] == 8 and font["h"] == 8
    assert len(font["glyphs"]) >= 0x60
    assert all(len(g) == 8 for g in font["glyphs"])
    # On the launcher there's no open cart, so sheet/tilemap are null; cart is null.
    assert obj["cart"] is None


def test_ws_roundtrip_pushes_frames_and_applies_input(server):
    """A real-localhost WebSocket round-trip against the HOST server -- the twin of the device's
    test_ws_end_to_end_over_localhost. Handshake on /ws, receive a PUSHED frame (which must
    replay to non-blank pixels -- the pixel cross-check over the wire), then push an input event
    UP the socket (tap tile 0) and assert the LIVE console reacts (launcher -> desktop), proving
    browser input reaches the real console. /assets then advertises the freshly opened cart's
    sheet + title (folding in the open-cart assets path)."""
    console, host, port = server
    assert console.ws.screen == "launcher"
    assert console.ws.launcher.items, "system carts should be seeded so a tile exists"
    # A launcher tap RUNS the cart (spec shell_ux_v1.md): this asserts a launcher ->
    # desktop transition over the wire.
    c = _WSClient(host, port)
    try:
        # A frame PUSHES down; it must replay to a non-blank 320x240 screen (the JS twin's path).
        # #76: in surfaces mode the flat cmds are DROPPED from the wire (the page composites
        # f.surfaces and ignores f.cmds; double-shipping would defeat the bandwidth win) and
        # the surfaces are DELTA-encoded per connection -- the FIRST frame to a fresh client
        # ships every surface in full, later frames stub unchanged ones as {"same": 1}.
        f = json.loads(c.recv_text().decode("utf-8"))
        assert f["cart"] is None                       # launcher home: no open cart yet
        assert "surfaces" in f and len(f["surfaces"]) >= 2, "the wire carries per-surface streams"
        assert f["cmds"] == [], "surfaces mode drops the flat cmds from the wire (#76)"
        assert not any(s.get("same") for s in f["surfaces"]), \
            "the first frame to a fresh client ships full surfaces"
        surf_cache = {}
        cv = Canvas(WIDTH, HEIGHT)
        web_view.replay_delta_surfaces_to_canvas(f["surfaces"], surf_cache, cv)
        assert len(set(cv.buf)) > 1, "the pushed frame must replay to a non-blank screen"
        # A follow-up frame on the same (static-ish launcher) screen deltas: at least one
        # surface arrives as a {"same":1} stub, and the cached replay still composites clean.
        f2 = json.loads(c.recv_text().decode("utf-8"))
        if "surfaces" in f2:
            assert any(s.get("same") for s in f2["surfaces"]), \
                "an unchanged surface must ship as a same-stub on the next frame (#76)"
            cv2 = Canvas(WIDTH, HEIGHT)
            web_view.replay_delta_surfaces_to_canvas(f2["surfaces"], surf_cache, cv2)
            assert len(set(cv2.buf)) > 1, "a delta frame must still composite a full screen"
        # Input pushes UP the same socket: tap the first real cart's card. Send down, let the
        # server drain it + step a frame or two with the tap held (held still = no drag, so the
        # release completes the tap), then release -- mirrors the down -> frame -> up order.
        tx, ty = _real_tile_center(console.ws)
        c.send_events([{"type": "down", "x": tx, "y": ty}])
        time.sleep(0.2)
        c.send_events([{"type": "up"}])
        deadline = time.time() + 5
        while console.ws.screen != "desktop" and time.time() < deadline:
            c.recv_text()                              # keep the socket drained while it transitions
        assert console.ws.screen == "desktop", (
            "browser input over the WebSocket must drive the live console")
    finally:
        c.close()
    # /assets now advertises the open cart's sheet + title (the open-cart assets path).
    _s, _ctype, obj = _get_json(host, port, "/assets")
    assert obj["cart"] is not None and obj["sheet"] is not None
    assert obj["sheet"]["tile"] == 8
    assert len(obj["sheet"]["pix"]) == obj["sheet"]["w"] * obj["sheet"]["h"]


def test_lan_url_falls_back_to_localhost(monkeypatch):
    """_lan_url uses the real LAN IP when available, else localhost -- never raises."""
    monkeypatch.setattr(web_console.host_app, "_real_local_ip", lambda: None)
    assert web_console._lan_url(8080) == "http://127.0.0.1:8080/"
    monkeypatch.setattr(web_console.host_app, "_real_local_ip", lambda: "10.0.0.5")
    assert web_console._lan_url(1234) == "http://10.0.0.5:1234/"


def test_host_reseeds_built_in_on_version_bump_preserving_moy_data(tmp_path):
    """The host store now honors cart version bumps (#47): a seeded built-in older than
    the shipped manifest version is re-seeded (code/art refreshed) while the kid's
    config.json / pmem.json are preserved -- matching the device. (Previously the host
    seeded once and ignored bumps, so a fixed built-in never reached the store.)"""
    import json
    import os
    from runtime import host_app
    carts = str(tmp_path / "carts")
    host_app._seed_system_carts(carts)
    dst = os.path.join(carts, "ocean.moy")              # ocean ships at version >= 2
    assert os.path.isdir(dst)
    # Make the seeded copy STALE (v0), plant a code marker + the kid's tuning.
    with open(os.path.join(dst, "manifest.json")) as f:
        man = json.load(f)
    man["version"] = 0
    with open(os.path.join(dst, "manifest.json"), "w") as f:
        json.dump(man, f)
    with open(os.path.join(dst, "main.py"), "w") as f:
        f.write("# stale kid edit\n")
    with open(os.path.join(dst, "config.json"), "w") as f:
        f.write('{"water": "indigo"}')
    host_app._seed_system_carts(carts)                    # shipped v2 > stale v0 -> re-seed
    with open(os.path.join(dst, "main.py")) as f:
        assert "stale kid edit" not in f.read()           # code refreshed
    with open(os.path.join(dst, "config.json")) as f:
        assert json.load(f) == {"water": "indigo"}        # kid tuning preserved
    assert host_app._manifest_version(dst) >= 2           # manifest refreshed to shipped


def test_page_alloc_resets_clip_on_resize():
    """Regression (big-canvas web view): the browser page must reset the clip
    window whenever it (re)allocates the canvas. The clip is first set at load
    with the 320x240 default; if alloc() doesn't reset it after /assets grows
    the canvas (e.g. a 960x600 system canvas, #39), every draw op stays clipped
    to the top-left 320x240 and the rest of the page renders black. The JS can't
    be unit-tested directly, so guard the served source: alloc() must call rs()."""
    page = web_view.PAGE_HTML
    start = page.index("function alloc()")
    body = page[start:start + 400]
    assert "rs()" in body, (
        "web_view.PAGE_HTML alloc() must call rs() to reset the clip on canvas "
        "resize; without it a >320x240 system canvas clips drawing to the top-left"
    )


# ---------------------------------------------------------------------------
# The windowed desktop over the web transport (#73: the webview as a WM tier).
# ---------------------------------------------------------------------------

def _replay_frames(console, screen, layers, atlas, assets, frames=2):
    """Step + replay `frames` like the browser: EVERY served frame lands in the
    persistent layers/atlas caches (ship-once deflayers span frames). A skipped
    (static, cmds=None) frame streams nothing -- the browser retains, so we do."""
    cmds = None
    for _ in range(frames):
        got, _cart, _au = console.step_frame()
        if got is None:
            continue
        cmds = got
        replay_to_canvas(cmds, screen, layers=layers, assets=assets, atlas=atlas)
    return cmds


def test_windowed_web_console_installs_the_windowed_wm(tmp_path):
    from runtime.wm_windowed import WindowedWM
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=2,
                                     windowed=True)
    assert isinstance(console.ws.wm, WindowedWM)
    # The WM anchors to the RECORDING canvas -- window streams reach the wire.
    assert console.ws.wm._root_canvas is console.canvas


def test_windowed_window_ships_as_a_recorded_layer(tmp_path):
    """An app window's buffer is a RecordingLayer: the browser receives its
    content as a deflayer + blit (the #54/#43 layer mechanism), and the replayed
    frame actually shows the window (pixels differ from the bare desktop)."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=2,
                                     windowed=True)
    ws = console.ws
    ws.pointer.visible = False
    screen = Canvas(1024, 600)
    layers = {}
    atlas = {}
    assets = console.assets()
    _replay_frames(console, screen, layers, atlas, assets)
    desktop = bytes(screen.buf)
    ws.open_settings()
    _replay_frames(console, screen, layers, atlas, assets)
    win = ws.wm._wins["settings"]
    assert hasattr(win.buf, "_lr")            # a RecordingLayer, not a raw canvas
    assert layers                              # its deflayer landed in the cache
    framed = bytes(screen.buf)
    assert framed != desktop                   # the window visibly composited
    # The window's border row exists at the window rect in the replayed pixels.
    row = framed[win.y * 1024 + win.x: win.y * 1024 + win.x + win.w]
    assert len(set(row)) >= 1 and framed != desktop


def test_windowed_playtest_streams_the_game_as_one_b64_img(tmp_path):
    """The player window's content ships as ONE scaled BASE64 img op per frame
    (the recording _blit_game fallback tags the frame as a paint image -- ~2.4x
    lighter on the wire than a JSON int-list spr), and the replayed screen shows
    the cart's pixels inside the window."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=2,
                                     windowed=True)
    ws = console.ws
    ws.pointer.visible = False
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it.get("path"))
    screen = Canvas(1024, 600)
    layers = {}
    atlas = {}
    assets = console.assets()
    ws.open()                                  # run the selected cart
    cmds = _replay_frames(console, screen, layers, atlas, assets, frames=4)
    assert ws.wm._order[-1] == "desktop"
    win = ws.wm._wins["desktop"]
    ox, oy, scale = ws.wm._player_view(win)
    # A full-game-frame b64 img at the viewport origin + scale is on the wire.
    imgs = [c for c in cmds if c[0] == "img" and c[1] == ox and c[2] == oy
            and c[3] == 320 and c[4] == 240 and len(c) > 6 and c[6] == scale]
    assert imgs, "expected the game frame as one scaled b64 img op"
    # And the replayed window region isn't flat black (the cart drew something).
    mid = (oy + 120 * scale) * 1024 + ox
    assert len(set(screen.buf[mid:mid + 320 * scale])) > 1


def test_scaled_img_replays_pixel_identical_to_raster_blit():
    """The ["img", ..., b64, scale] op (the b64 full-frame composite) replays
    pixel-identically to the raster scaled blit it replaces."""
    from runtime.widgets import _Blit
    from runtime.canvas import Image
    src = Canvas(8, 6)
    for i in range(8 * 6):
        src.buf[i] = i % 64
    # Raster reference: the plain scaled spr blit.
    want = Canvas(64, 48)
    want.cls(0)
    want.spr(Image(8, 6, list(src.buf), transparent=None), 5, 3, 4)
    # The wire: record via the paint-tagged path, replay.
    cc = CommandCanvas(64, 48)
    img = _Blit(8, 6, bytes(src.buf), -1)
    img._paint = True
    cc.cls(0)
    cc.spr(img, 5, 3, 4)
    cmds = cc.take_commands()
    assert any(c[0] == "img" and len(c) > 6 and c[6] == 4 for c in cmds)
    got = Canvas(64, 48)
    replay_to_canvas(cmds, got)
    assert bytes(got.buf) == bytes(want.buf)


def test_idle_static_screen_pushes_nothing(tmp_path):
    """A static screen (nothing dirty, no animation) makes step_frame return None
    -- the WS loop then pushes nothing, so an idle desktop is ~free over a VPN. A
    page (re)connect re-arms a full keyframe via its /assets fetch."""
    console = web_console.WebConsole(str(tmp_path / "carts"), fps=30,
                                     sys_size=(1024, 600), font_scale=2,
                                     windowed=True)
    ws = console.ws
    ws.pointer.visible = False
    ws.select_wallpaper("fill:black", persist=False)   # static backdrop
    cmds, _cart, _au = console.step_frame()
    assert cmds                                         # first frame: the keyframe
    # Let the shelf's one-cover-per-frame budget settle, then idle is free.
    for _ in range(160):
        cmds, _cart, _au = console.step_frame()
        if cmds is None:
            break
    for _ in range(3):
        cmds, _cart, _au = console.step_frame()
        assert cmds is None                             # static -> nothing on the wire
    console.assets()                                    # a page (re)connects
    cmds, _cart, _au = console.step_frame()
    assert cmds                                         # -> one full keyframe again


def test_hop_quest_mapped_layer_background_replays_identical(tmp_path):
    """Hop Quest paints its terrain with lay.map() into the pre-rendered background
    layer (the #66 "paint the static background once" habit). RecordingLayer.map must
    ship the cells (per-cell spr expansion) -- the old __getattr__ fallthrough rastered
    the terrain but shipped a cls-only deflayer, so the browser drew a flat sky with no
    ground. The running cart must replay pixel-identical, with the layer shipped ONCE."""
    ws, drv, tee = _build_tee(str(tmp_path / "carts"))
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == "Hop Quest":
            ws.launcher.sel = i
            ws.open()
            break
    layers = {}
    for f in range(3):
        ws.input.begin_frame()
        ws.frame(1.0 / 30)
        cmds = _served(tee)
        cv = Canvas(WIDTH, HEIGHT)
        replay_to_canvas(cmds, cv, layers)
        assert bytes(cv.buf) == bytes(tee.raster.buf), \
            "Hop Quest frame %d must replay pixel-identical over the web stream" % f
        defl = [c for c in cmds if c[0] == "deflayer"]
        if f == 0:
            assert len(defl) == 1, "the background layer ships exactly once"
            assert len(defl[0][4]) > 50, \
                "the shipped layer must carry the terrain cells, not just the sky cls"
        else:
            assert not defl, "later frames blit the shipped layer by reference"
    assert ws.cart_error is None


def test_effective_input_hint_never_hides_the_keyboard_in_the_editor(tmp_path):
    """#42 Thread 3 regression: a buttons-only cart's manifest hint must apply
    only while the cart OWNS the keyboard (playing). Opening the same cart for
    CHANGE (the Editor -- typing!) must report None so the phone page keeps its
    soft-keyboard summon; PLAY from the editor re-applies the hint, and exiting
    back drops it again."""
    from runtime import host_app, web_view
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    for i, c in enumerate(ws.picker.items):
        if c.get("title") == "Battle City":
            ws.picker.sel = i
            break
    ws.open_picker()
    ws.pick_selected()
    assert ws.screen == "menu"
    assert tuple(ws.cart.get("input")) == ("buttons",)      # the manifest hint
    assert web_view.effective_input_kinds(ws) is None       # ...but the EDITOR types
    ws.run(ws.project, ws.editor_app)                       # PLAY
    assert ws.screen == "desktop"
    assert tuple(web_view.effective_input_kinds(ws)) == ("buttons",)
    ws._exit_to_caller()
    assert web_view.effective_input_kinds(ws) is None       # back in the editor

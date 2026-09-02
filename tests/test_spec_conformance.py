"""SPEC.md 11 conformance: the host rasterizer against the spec's golden frames.

> *An implementation conforms when it runs the conformance suite and produces
> pixel-identical output.*

Moybyte is the reference console, so it should be able to make that claim about
itself in its own test run rather than from a sibling checkout. The suite is
vendored (see spec_conformance/UPSTREAM.md for why); this replays each scene's
recorded verb trace through the host's canvas -- `device_canvas.DeviceCanvas`
over a `HostCompositor`, the same class both boards and the browser run -- and
compares the SHA-256 of the resulting index framebuffer to the golden hash.

WHAT A FAILURE HERE MEANS. Not "a test broke" -- the frames were rendered by
moycore and independently confirmed by two other implementations, so a mismatch
is moybyte disagreeing with the spec about what a verb draws. That is either a
regression or a spec change to follow, and `hashes.json`'s provenance field is
what makes it arguable rather than a coin toss.

WHAT IT DOES NOT REACH. The kernel under this canvas is `runtime/gfx_binding`:
libmoy compiled RGB565 and reached by ctypes -- the same SOURCE the boards
compile, but not the same binary, not MicroPython, and not a panel.
`tools/p4_conformance.py` runs these very carts on a board over serial, and is
the only check that touches the real kernel and the real glass.
"""

import hashlib
import json
import os

import pytest

from runtime import editors_sheet
from runtime import host_canvas

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "spec_conformance")

# SPEC.md 1: the console is a fixed-size machine.
W, H = 320, 240
# SPEC.md 3.2: the cart sheet is 16 x 32 tiles (128 x 256 px).
SHEET_COLS, SHEET_ROWS = 16, 32


def _load_hashes():
    with open(os.path.join(HERE, "hashes.json")) as fh:
        return json.load(fh)


def _scene_names():
    """Every scene in the vendored suite, core ones first. `core` marks the
    thirteen SPEC.md 6 scenes that count; the provisional ones (6.1's 3D verbs)
    are run too but reported separately -- 6.1 promotes verbs on evidence, and
    which implementations carry them IS the evidence."""
    return [(s["name"], bool(s.get("core")), s["frame_sha256"])
            for s in _load_hashes()["scenes"]]


def _build_assets(scene):
    """The sheet and tilemap the scene draws from -- loaded out of the vendored
    CART with moybyte's own deserialisers, so this exercises the real asset path
    rather than a fixture built to match."""
    cart_dir = os.path.join(HERE, "carts", scene + ".moy")
    sheet = editors_sheet.SpriteSheet(SHEET_COLS, SHEET_ROWS)
    tilemap = editors_sheet.TileMap(20, 15)
    gfx = os.path.join(cart_dir, "sprites.moygfx")
    if os.path.exists(gfx):
        with open(gfx) as fh:
            sheet = editors_sheet.SpriteSheet.from_hex(fh.read(), SHEET_COLS,
                                                       SHEET_ROWS)
    mp = os.path.join(cart_dir, "map.moymap")
    if os.path.exists(mp):
        with open(mp) as fh:
            tilemap = editors_sheet.TileMap.from_hex(fh.read())
    return sheet, tilemap


def _text_arg(a):
    """A print() argument in trace form. JSON cannot hold byte 0xFF inside a
    string and a trace must survive any language's parser, so anything past
    ASCII travels as a list of byte values (the text_bytes scene prints 0xFF on
    purpose -- and losing that frame is a bug this suite has caught before)."""
    if isinstance(a, list):
        return bytes(a)
    return a


def replay(calls, canvas, sheet=None, tilemap=None):
    """Run a trace against a Canvas. The spec's own replayer, transcribed --
    ~40 lines in any language, which is the point of publishing traces at all.
    Verbs are CART-facing (`spr(n, ...)`), because that is what SPEC.md
    specifies and what a Lua cart calls."""
    for call in calls:
        verb, a = call[0], call[1:]
        if verb == "cls":
            canvas.cls(a[0])
        elif verb == "pix":
            canvas.pix(a[0], a[1], a[2])
        elif verb == "line":
            canvas.line(a[0], a[1], a[2], a[3], a[4])
        elif verb == "rect":
            canvas.rect(a[0], a[1], a[2], a[3], a[4])
        elif verb == "rectb":
            canvas.rectb(a[0], a[1], a[2], a[3], a[4])
        elif verb == "circ":
            canvas.circ(a[0], a[1], a[2], a[3])
        elif verb == "circb":
            canvas.circb(a[0], a[1], a[2], a[3])
        elif verb == "tri":
            canvas.tri(a[0], a[1], a[2], a[3], a[4], a[5], a[6])
        elif verb == "trib":
            canvas.trib(a[0], a[1], a[2], a[3], a[4], a[5], a[6])
        elif verb == "print":
            canvas.print(_text_arg(a[0]), a[1], a[2], a[3])
        elif verb == "camera":
            canvas.camera(*a)
        elif verb == "clip":
            canvas.clip(*a)
        elif verb == "pal":
            canvas.pal(*a)
        elif verb == "palt":
            canvas.palt(*a)
        elif verb == "spr":
            canvas.spr_tile(sheet, a[0], a[1], a[2], a[3], a[4], a[5])
        elif verb == "sspr":
            canvas.sspr(sheet, a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7],
                        a[8], a[9])
        elif verb == "tline":
            canvas.tline(tilemap, sheet, a[0], a[1], a[2], a[3], a[4], a[5],
                         a[6], a[7], a[8])
        elif verb == "map":
            canvas.map(tilemap, sheet, a[0], a[1], a[2], a[3], a[4], a[5],
                       a[6], a[7])
        else:
            raise ValueError("unknown trace verb %r" % (verb,))


def _index_frame(c):
    """The canvas as one byte of palette INDEX per pixel, whatever it stores.

    THE GOLDENS ARE NOT OURS TO MOVE. `hashes.json` is vendored verbatim from
    moy-spec and `test_the_vendored_suite_is_the_spec_suite_when_a_checkout_is_
    present` below fails if it drifts, so when the host canvas becomes RGB565
    the answer is to convert BACK here -- never to re-record.

    The conversion is exact, not best-effort: MOY64's 64 entries map to 64
    DISTINCT RGB565 words, so the reverse LUT is total (checked below, because
    a collision would silently merge two colours into one hash). It reads the
    canvas's own reverse table, which is built from PAL565_WIRE and is
    therefore byte-order-correct by construction -- under CPython that order is
    the T-Deck's byte-SWAPPED one, since `moy_dsi` is absent.

    A word no palette index produced has no honest index to become. That should
    be impossible in a trace replay (every verb here resolves through the
    palette), so it RAISES rather than mapping to 0 -- a silent 0 would move
    the hash and look like a raster bug. (`device_canvas.to_indices` is the
    shipped version of this conversion and is strict for the same reason; the
    loop stays open-coded here so the assertion messages can say WHICH pixel and
    WHY, which is what makes a golden mismatch arguable.)
    """
    buf = getattr(c, "buf", None)
    if buf is not None and len(buf) == W * H:
        return bytes(buf)                       # already an index buffer
    raw = getattr(c, "_buf", None)
    if raw is None or len(raw) != W * H * 2:
        raise AssertionError(
            "canvas exposes neither a %dx%d index buffer nor a 565 one"
            % (W, H))
    import device_canvas as _dc
    rev = _dc._PAL565_INDEX
    assert len(rev) == 64, (
        "the RGB565 reverse LUT holds %d of 64 entries -- two palette colours "
        "share a word, so this conversion would merge them" % len(rev))
    words = memoryview(raw).cast("H")
    out = bytearray(W * H)
    for i, w in enumerate(words):
        idx = rev.get(w)
        if idx is None:
            raise AssertionError(
                "pixel %d holds 0x%04X, which no palette index produces -- the "
                "frame cannot be reduced to indices for the golden hash"
                % (i, w))
        out[i] = idx
    return bytes(out)


def render(scene, canvas=None):
    """Replay one scene and return its index framebuffer as bytes."""
    with open(os.path.join(HERE, "traces", scene + ".json")) as fh:
        calls = json.load(fh)
    sheet, tilemap = _build_assets(scene)
    c = canvas if canvas is not None else host_canvas.make_canvas(W, H)
    replay(calls, c, sheet, tilemap)
    flush = getattr(c, "flush_batch", None)
    if flush is not None:
        flush()          # the console auto-batches sprites; the goldens do not
    return _index_frame(c)


# The scenes moy-spec added on 2026-09-02 for verbs the PYTHON tier does not
# draw yet -- fillp, oval/ovalb, sset, the screen palette, map(..., layers).
# The Lua tier has every one of them through libmoy (re-vendored the same
# day); the host canvas and cart_api are moybyte's own next step, and these
# are strict: the day a twin lands, its scene flips from xfail to a failure
# that says "remove me from this set".
PYTHON_TIER_PENDING = {"oval", "fillp", "sheet", "screen_pal", "flags"}


def _scene_params():
    out = []
    for name, core, golden in _scene_names():
        marks = ()
        if name in PYTHON_TIER_PENDING:
            marks = (pytest.mark.xfail(
                strict=True,
                reason="the Python tier has no %s verbs yet (SPEC.md 2026-09); "
                       "the Lua tier draws this scene through libmoy" % name),)
        out.append(pytest.param(name, core, golden, id=name, marks=marks))
    return out


@pytest.mark.parametrize("scene,core,golden", _scene_params())
def test_scene_is_pixel_identical_to_the_spec_golden(scene, core, golden):
    frame = render(scene)
    assert len(frame) == W * H, (
        "%s produced %d bytes, not a %dx%d index framebuffer"
        % (scene, len(frame), W, H))
    got = hashlib.sha256(frame).hexdigest()
    if got == golden:
        return
    # Say WHERE, not just that. A hash tells you nothing about a one-pixel
    # rounding difference on a circle edge, which is the likeliest failure.
    pytest.fail("%s: %s\n  golden %s\n  got    %s\n"
                "  regenerate diffs with moy-spec:\n"
                "    python3 conformance/run.py --diff out/"
                % (scene, "core scene (SPEC.md 6)" if core
                   else "provisional scene (SPEC.md 6.1)", golden, got))


def test_every_core_scene_is_present_and_counted():
    """The suite is only a gate if it is whole. A vendored copy that quietly
    lost a scene would pass every remaining one."""
    names = _scene_names()
    core = [n for n, is_core, _ in names if is_core]
    assert len(core) == 13, "expected SPEC.md's 13 core scenes, found %r" % (core,)
    for name, _, _ in names:
        assert os.path.exists(os.path.join(HERE, "traces", name + ".json")), name
        assert os.path.isdir(os.path.join(HERE, "carts", name + ".moy")), name


def test_the_vendored_suite_is_the_spec_suite_when_a_checkout_is_present():
    """If a moy-spec checkout is beside this one, the vendored copy must match
    it. Catches the failure mode vendoring introduces -- going stale without
    anyone noticing -- while staying a no-op in CI and in a fresh clone."""
    spec = os.environ.get("MOY_SPEC") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "moy-spec")
    upstream = os.path.join(spec, "conformance", "golden", "hashes.json")
    if not os.path.exists(upstream):
        pytest.skip("no moy-spec checkout to compare against")
    with open(upstream) as fh:
        theirs = json.load(fh)
    ours = _load_hashes()
    assert {s["name"]: s["frame_sha256"] for s in ours["scenes"]} == \
           {s["name"]: s["frame_sha256"] for s in theirs["scenes"]}, (
        "the vendored conformance goldens have drifted from moy-spec -- "
        "re-vendor (tests/spec_conformance/UPSTREAM.md) and check what moved")

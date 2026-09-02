"""The GAME FOLD, executed (`native/moy_flush/moy_fold.c`) and pinned.

THE FOLD. A cart with a small canvas (SPEC.md 1/3.1) is normally composited
into the root framebuffer -- black bezels plus one integer upscale -- and the
banded flush then reads that same root back out of PSRAM to fill its bounce
slots. Both passes move the whole screen. The fold deletes the first: the
canvas snapshots the game rectangle into a flush-private scratch and ARMS the
fold, and the core-0 feeder synthesizes every band straight from that snapshot.
Shipped on the T-Deck 2026-08-11 (#190, cmp 7.2 -> 1.8 ms on glass), dropped
when its flush moved to the shared feeder, and taken by BOTH boards in 2026-09
over `moy_fold` -- with the Guition's scale-2 case, which its C used to decline.

WHY THE C IS THE THING UNDER TEST. The synthesis runs on the FEEDER task: no MP
context, no GIL, no exceptions, reading a buffer that another core is about to
overwrite. None of that is reachable from Python, and a Python transcription of
it would be a second body that can agree with this file while the shipped C
disagrees with both -- the recorded 2026-08-06 `provisional_tline` failure. So
the REAL file is compiled unmodified against `tests/moy_flush_harness/`'s stub
ESP-IDF and driven by the scenarios named below; `tests/test_moy_flush_c.py`
owns the build and the engine's own scenarios, and this file owns the `fold_`
ones. There is no MicroPython and no board in any of it.

WHAT MAKES THE PIXEL CHECK REAL: the fold has an ORACLE. A folded band must
reassemble, byte for byte, into `moy_fold_composite` -- the very composite the
disarm path performs when an overlay lands on top -- so "the fold is
pixel-identical to the path it replaces" is an assertion and not a hope. On
glass the same claim is `moy_lcd.fold_test` / `moy_axs.fold_test`, which
compare against each board's shipped ROOT-copy gather.

The rest here is source-order routing: the frame walk must disarm before
anything paints the root above the game, the canvas must fence before it
overwrites the scratch, and a banded board WITHOUT the lever must carry no fold
attribute at all. `tests/test_banded_panel.py` owns the compositors' behaviour.
"""

import subprocess
from pathlib import Path

import pytest

from test_moy_flush_c import SCENARIOS, require_harness

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "device"
TDECK = ROOT / "firmware" / "lilygo_t_deck_plus_mainline"
GUITION = ROOT / "firmware" / "guition_jc3248w535"

FOLD_SCENARIOS = [s for s in SCENARIOS if s.startswith("fold_")]


@pytest.mark.parametrize("scenario", FOLD_SCENARIOS)
def test_scenario(scenario):
    """One fold scenario, driven through the real C in its own process."""
    exe = require_harness()
    proc = subprocess.run([exe, scenario], capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, "\n" + proc.stdout + proc.stderr
    assert proc.stdout.startswith("PASS "), proc.stdout


def test_the_scenarios_cover_the_fold_as_a_whole():
    """A ratchet on the list: the latch, the fence, the disarm, the meter and
    BOTH gathers. A scenario deleted from the harness should turn this red
    rather than shrink the run silently -- the same rule the engine's own list
    carries, and for the same reason."""
    require_harness()
    for name in ("fold_arm_geometry", "fold_latch_is_one_shot",
                 "fold_disarm_performs_the_composite",
                 "fold_bands_match_the_composite",
                 "fold_rot_bands_match_the_composite",
                 "fold_fence_waits_for_the_feed",
                 "fold_reset_keeps_the_meter"):
        assert name in FOLD_SCENARIOS, name


def test_the_harness_drives_the_shipped_fold_and_not_a_copy():
    """The whole value of the scenarios is that the SHIPPED file runs."""
    require_harness()
    fold = (ROOT / "native" / "moy_flush" / "moy_fold.c").read_text(
        encoding="utf-8")
    for symbol in ("moy_fold_arm", "moy_fold_consume", "moy_fold_fence",
                   "moy_fold_composite", "moy_fold_band",
                   "moy_fold_band_rot"):
        assert symbol in fold
    harness = ROOT / "tests" / "moy_flush_harness"
    for path in sorted(harness.rglob("*.c")) + sorted(harness.rglob("*.h")):
        src = path.read_text(encoding="utf-8")
        for symbol in ("void moy_fold_band", "void moy_fold_composite",
                       "bool moy_fold_arm"):
            assert symbol not in src, "%s re-implements %s" % (path.name,
                                                               symbol)


def test_both_banded_boards_link_the_shared_fold():
    """Neither panel module may grow its own copy of the synthesis. The C is
    staged by `[native.shared]`, so what a grep can pin is that each board
    INCLUDES it and calls it rather than hand-rolling a second gather."""
    axs = (GUITION / "native" / "moy_axs" / "modmoy_axs.c").read_text(
        encoding="utf-8")
    lcd = (TDECK / "native" / "moy_lcd" / "modmoy_lcd.c").read_text(
        encoding="utf-8")
    for src in (axs, lcd):
        assert '#include "moy_fold.h"' in src
        assert "moy_fold_arm(" in src          # the verb refuses geometry here
        assert "moy_fold_consume()" in src     # the one-shot latch, feeder idle
        assert "moy_fold_end()" in src         # ...released at frame_end
        assert "moy_fold_composite(" in src    # the disarm's deferred composite
    # The gathers are the shared body's; the boards only choose which.
    assert "moy_fold_band_rot(" in axs and "moy_fold_band(" not in axs
    assert "moy_fold_band(" in lcd and "moy_fold_band_rot(" not in lcd


def test_the_arm_is_consumed_with_the_feeder_idle():
    """THE one ordering rule outside moy_fold.c: `moy_fold_consume` latches
    `armed` into `inflight`, and a band reads `inflight`. Both boards must
    therefore drain BEFORE they consume, or the feeder can see the latch move
    under it -- the class of race moy_flush.h says every clause of the handoff
    was."""
    for path in (GUITION / "native" / "moy_axs" / "modmoy_axs.c",
                 TDECK / "native" / "moy_lcd" / "modmoy_lcd.c"):
        src = path.read_text(encoding="utf-8")
        for verb in ("kick", "show"):
            head = src.index("static mp_obj_t moy_%s_%s("
                             % ("axs" if "axs" in path.name else "lcd", verb))
            body = src[head:src.index("\n}\n", head)]
            assert "moy_flush_drain()" in body, (path.name, verb)
            assert body.index("moy_flush_drain()") \
                < body.index("moy_flush_kick("), (path.name, verb)
            assert body.index("moy_flush_kick(") \
                > body.index("_decide_window()" if "axs" in path.name
                             else "moy_fold_consume()"), (path.name, verb)


def test_frame_walk_disarms_above_the_game():
    """Source-order pins for the shared console hooks: the composite arms
    (device-side), and the walk disarms before any layer that can paint the
    root above it -- including the deferred-transition loading toast. Without
    this the overlay lands on a root the flush is about to ignore, i.e. it is
    invisible."""
    src = (ROOT / "runtime" / "console.py").read_text(encoding="utf-8")
    walk = src[src.index("_fold_live = False"):]
    assert walk.index("self._composite_game()") \
        < walk.index("disarm_scale_fold") \
        < walk.index("self._draw_loading_toast()")


def test_blit_game_fences_before_it_overwrites_the_scratch():
    """The canvas rewrites the flush-private scratch every play frame, and the
    previous frame's bands may still be reading it. The fence is normally two
    compares in C; being cheap is not the same as being optional."""
    dc = (DEVICE / "device_canvas.py").read_text(encoding="utf-8")
    body = dc[dc.index("def blit_game"):]
    assert body.index("fold_fence") < body.index("arm_scale_fold")
    assert '"fold_supported", False' in body


def test_a_banded_board_without_the_lever_carries_no_fold_attribute():
    """`FoldingCompositor` is a SUBCLASS and not four methods on the shared
    base, because `blit_game`, `_diag_pump` and the dev channel's `state` all
    probe these names with getattr: a board that cannot synthesize bands must
    have NO fold attribute, since a `fold_count` of 0 inherited from a base is
    indistinguishable from a fold that never fires."""
    base = (DEVICE / "banded_panel.py").read_text(encoding="utf-8")
    _prose, _, body = base.partition("class BandedCompositor")
    head, _, folding = body.partition("class FoldingCompositor")
    assert folding, "FoldingCompositor is where the fold lives"
    for name in ("fold_supported", "fold_count", "arm_scale_fold",
                 "disarm_scale_fold", "fold_fence"):
        assert name not in head, "%s leaked onto BandedCompositor" % name
        assert name in folding, name

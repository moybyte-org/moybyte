"""The input table's C (native/moy_input), below both bindings: the fuzz walk
under the sanitizers, the latches a board's writers use, and the native
module on the desktop MicroPython agreeing with the host's binding."""

import os
import shutil
import subprocess

import pytest

from runtime import moy_input as mi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT = os.path.join(ROOT, "native", "moy_input")
SPINE = os.path.join(ROOT, "native", "moy_spine")


def test_the_fuzz_walk_holds_under_the_sanitizers(tmp_path):
    cc = shutil.which(os.environ.get("CC", "cc"))
    if cc is None:
        pytest.skip("no C compiler")
    exe = str(tmp_path / "fuzz_input")
    build = subprocess.run(
        [cc, "-std=c99", "-g", "-O1", "-fsanitize=address,undefined",
         "-fno-sanitize-recover=all", "-I", INPUT, "-I", SPINE,
         "-I", os.path.join(ROOT, "native", "moy_kernel"),
         os.path.join(INPUT, "fuzz_input.c"), os.path.join(INPUT, "moy_input.c"),
         os.path.join(INPUT, "moy_hid.c"),
         "-o", exe],
        capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    out = subprocess.run([exe, "1", "4000", "12"], capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]


def test_a_one_shot_key_is_delivered_for_one_frame_with_a_gap_between_equal_keys():
    t = mi.InputTable()
    kbd = t.source("kbd")
    for k in (0x61, 0x61, 0x62):
        kbd.key(k)
    seen = []
    for _ in range(6):
        t.begin_frame()
        seen.append(t.last_key)
    assert seen == [0x61, 0, 0x61, 0x62, 0, 0]


def test_a_level_write_drops_the_sources_queue():
    t = mi.InputTable()
    kbd = t.source("kbd")
    kbd.key(0x61)
    kbd.last_key = 0x77
    t.begin_frame()
    assert t.last_key == 0x77
    t.begin_frame()
    assert t.last_key == 0x77, "a level stays until the source writes again"


def test_the_press_is_published_once_a_press_whoever_reads_it():
    """THE PRESS (moy_input.h): a held level is one press, a byte after a
    frame with none is a press again, a one-shot key is one press, and a
    Ctrl chord is its control byte."""
    t = mi.InputTable()
    kbd = t.source("kbd")
    seen = []
    for k in (0x61, 0x61, 0, 0x61, 0x62, 0x62):
        kbd.last_key = k
        t.begin_frame()
        seen.append(t.typed)
        seen.append(t.typed)            # a second reader sees the same press
    assert seen == [0x61, 0x61, 0, 0, 0, 0, 0x61, 0x61, 0x62, 0x62, 0, 0]
    kbd.last_key = 0
    t.begin_frame()
    kbd.key(0x63)
    t.begin_frame()
    assert t.typed == 0x63
    t.begin_frame()
    assert t.typed == 0
    kbd.last_key = mi.event(ord("z"), mi.CTRL)
    t.begin_frame()
    assert t.typed == 0x1A


def test_spending_the_press_swallows_the_byte_held_until_it_is_pressed_again():
    t = mi.InputTable()
    kbd = t.source("kbd")
    kbd.last_key = 0x0D
    t.begin_frame()
    assert t.typed == 0x0D
    t.spend_key()                       # a surface took the keyboard on it
    assert t.typed == 0
    t.begin_frame()
    assert t.typed == 0, "the byte held as it was spent is no press"
    kbd.last_key = 0
    t.begin_frame()
    kbd.last_key = 0x0D
    t.begin_frame()
    assert t.typed == 0x0D, "let go and pressed again, it types"
    kbd.last_key = 0x71                 # down between two merges, then spent
    t.spend_key()
    t.begin_frame()
    assert t.typed == 0
    t.begin_frame()
    t.spend_edges()                     # the frame's edges take the press too
    kbd.last_key = 0
    t.begin_frame()
    kbd.last_key = 0x72
    t.begin_frame()
    t.spend_edges()
    assert t.typed == 0


def test_a_direct_write_is_the_frames_press_against_the_last_merge():
    t = mi.InputTable()
    t.begin_frame()
    t.last_key = 0x61                   # written after the merge: this frame's
    assert t.typed == 0x61
    t.last_key = 0
    assert t.typed == 0
    t.begin_frame()
    t.last_key = 0x62                   # written before the merge: the merge's
    t.begin_frame()
    assert t.typed == 0x62
    t.last_key = 0x62
    assert t.typed == 0, "the byte the last merge saw is no press"


def test_the_sources_are_bounded_and_say_so():
    t = mi.InputTable()
    for i in range(mi.SOURCES - 1):
        t.source("s%d" % i)
    with pytest.raises(OSError):
        t.source("one-too-many")
    with pytest.raises(ValueError):
        t.source("p", player=mi.PLAYERS)


def test_masks_for_a_player_no_source_sits_on_are_empty():
    t = mi.InputTable()
    t.source("pad", 1).set_held("a", True)
    t.begin_frame()
    assert t.button_masks(mi.NAMES, 1) == (16, 16)
    assert t.button_masks(mi.NAMES, 2) == (0, 0)
    assert t.button_masks(mi.NAMES, 0) == (0, 0)
    assert t.button_masks(mi.NAMES) == (16, 16)


def test_a_new_vm_forgets_every_wrapper_the_last_one_cached():
    """A soft reset re-initialises the heap, not the root pointers: each root
    the binding registers is cleared when the kernel starts the next VM, or
    the next `kernel()` hands back a word of the new heap (a Ctrl-D once met
    `'list' object has no attribute 'pointer'`)."""
    import re
    with open(os.path.join(INPUT, "modmoy_input.c")) as f:
        binding = f.read()
    with open(os.path.join(ROOT, "native", "moy_kernel", "moy_kernel.c")) as f:
        kernel = f.read()
    roots = re.findall(r"MP_REGISTER_ROOT_POINTER\(mp_obj_t (\w+)\);", binding)
    fresh = binding.split("void moy_input_vm_fresh(void) {", 1)[1].split("}", 1)[0]
    assert roots and all("MP_STATE_VM(%s) = MP_OBJ_NULL;" % r in fresh for r in roots)
    start = kernel.split("soft_reset:", 1)[1]
    assert start.index("mp_init();") < start.index("moy_kernel_vm_fresh();") \
        < start.index("pyexec_frozen_module")

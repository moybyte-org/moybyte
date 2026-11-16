"""The hand-written bindings are a closed list (#260 A5).

Every MicroPython module a C file under native/ or a board's own native/
registers is bound one of two ways:

  * generated: its face.json has rows, and at least one row or constant is
    written by tools/gen_os/face.py (tools/gen_os/face.py `generated`); the
    face's `hand` rows are still named there and checked by
    tests/test_face_parity.py;
  * by hand: it is a key of HAND_WRITTEN below, with the reason its
    MicroPython face (and its ctypes face under runtime/host/, where it has
    one) is written by hand.

A new module therefore arrives with a generated face or with an entry here
that says why not. An entry leaves when its module is generated or deleted,
and the test fails until it does; nothing else removes one."""

import os
import re
import subprocess

from tools.gen_os import face as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HAND_WRITTEN = {
    # -- object types and callbacks -------------------------------------------
    "moy_app": "the app ABI's object types (App, Surface, Damage, Theme, Prefs), "
               "their iterators and the Python callbacks an app hands the C; the "
               "role rows are generated from native/os_calls.json (`roles`)",
    "moy_gfx": "object types (DrawCtx and the sprite and draw gates) and Python "
               "callbacks, over buffers the drawing kernels write in place",
    "moy_glass": "object types (Canvas, Buf) that own pixels and their presenters, "
                 "with Python callbacks",
    "moy_play": "object types (Tick, Match) and the Player's per-frame rows, "
                "the hot path the generator leaves by hand",
    "moy_sh": "object types (Screen, Job, Face) over the C shell's state",
    "moy_ui": "the toolkit's engine rows, buffer-heavy and on the frame path; "
              "its engine verbs are generated (`api`, tools/gen_os/ui.py)",
    "moy_guard": "runs a Python callback under the scheduler's slice and stops it "
                 "(nlr), which no generated row can express",
    "moy_loop": "the frame loop calls its registered Python upcalls; its meters "
                "answer dicts and tuples",
    "moy_elf": "loads and steps compiled apps whose service rows call back into "
               "Python; host face registered by its file (`registered: false`)",
    "moy_wasm": "a run's start, step and service rows call back into Python; "
                "native/moy_wasm_web registers the web build's moy_wasm over "
                "the same face",
    "moycore": "the Lua tier's runs and frames: rows call into the VM under nlr "
               "and raise its errors",
    "moy_c6": "a callback scheduled from the ESP-NOW receive task "
              "(mp_sched_schedule); board only",
    "moy_store": "the store's kv volume, journal and shelf: rows answer dicts and "
                 "call back into Python under nlr; every name is a `hand` row of "
                 "its face",
    # -- memory and buffers the generator has no kind for ---------------------
    "moy_alloc": "answers memoryviews over heap_caps memory and frees by view; "
                 "board only; every name is a `hand` row of its face",
    "moy_ppa": "the P4's PPA: rows take canvases and framebuffers by the buffer "
               "protocol and run asynchronous transfers; board only",
    "moy_dsi": "the P4's panel: answers its framebuffers as memoryviews; board "
               "only",
    "moy_web": "answers the baked console's assets as memoryviews over the "
               "image; board only",
    "moy_prof": "starts a timer ISR's sampler and answers its tables; board only; "
                "every name is a `hand` row of its face",
    # -- devices and streams, not yet converted -------------------------------
    "moy_gatt": "the BLE central and the sim_* rows that drive its stand-in ops; "
                "not yet converted",
    "moy_lora": "the LoRa radio and the sim_* rows that drive its stand-in ops; "
                "not yet converted",
    "moy_usb": "the P4's USB host and the sim_* rows that drive its stand-in ops; "
               "not yet converted",
    "moy_media": "playback rows that feed and pump a stream into the audio ring "
                 "from Python; not yet converted",
    "moy_audio": "the mixer: one hand binding() closure on the host "
                 "(runtime/host/audio_binding.py); not yet converted",
    # -- small board-only rows, not yet converted -----------------------------
    "moy_kernel": "the boot verdict, watchdog and VM rows, answering dicts and "
                  "tuples; board only; not yet converted",
    "moy_crash": "the crash record kept across a reset, registered from "
                 "modmoy_kernel.c; board only; not yet converted",
    "moy_png": "two rows over moy_png.h; board only; not yet converted",
    "moy_sd": "five sector rows over moy_sd.h; board only; not yet converted",
    "moy_serial": "two rows over the console's serial line; board only; not yet "
                  "converted",
    "moy_ssh": "the SSH server's start, stop and stats; board only; not yet "
               "converted",
    "moy_tty": "two rows over moy_tty.h; board only; not yet converted",
    "moy_usbdisk": "three rows over moy_usbdisk.h; board only; not yet converted",
    # -- a board's own driver, outside native/ --------------------------------
    "moy_axs": "the Guition S3's panel and touch driver, in its board dir with "
               "no face.json: ESP-IDF calls over pixel buffers",
    "moy_lcd": "the T-Deck's panel driver, in its board dir with no face.json: "
               "ESP-IDF calls over pixel buffers",
}

REG = re.compile(r"^\s*MP_REGISTER_MODULE\(MP_QSTR_(\w+),", re.M)


def registered():
    """{module: [C files registering it]} over native/ and firmware/*/native/."""
    files = subprocess.check_output(
        ("git", "-C", ROOT, "ls-files", "native/*.c", "firmware/*/native/*.c")).decode().split()
    out = {}
    for rel in files:
        with open(os.path.join(ROOT, rel), errors="replace") as f:
            for m in REG.findall(f.read()):
                out.setdefault(m, []).append(rel)
    return out


def _generated():
    return {m for m, face in F.load().items() if "rows" in face and F.generated(face)}


def test_every_module_is_generated_or_on_the_list():
    gen = _generated()
    missing = sorted("%s (%s)" % (m, ", ".join(fs)) for m, fs in registered().items()
                     if m not in gen and m not in HAND_WRITTEN)
    assert not missing, (
        "a new C module needs a generated face (rows in its face.json, "
        "tools/gen_os/face.py) or an entry in HAND_WRITTEN saying why not: %s" % missing)


def test_an_entry_leaves_when_its_module_is_generated_or_gone():
    gen, reg = _generated(), registered()
    converted = sorted(m for m in HAND_WRITTEN if m in gen)
    assert not converted, "generated now; take them off HAND_WRITTEN: %s" % converted
    gone = sorted(m for m in HAND_WRITTEN if m not in reg)
    assert not gone, "no C registers them; take them off HAND_WRITTEN: %s" % gone


def test_every_entry_says_why():
    for m, why in HAND_WRITTEN.items():
        assert isinstance(why, str) and len(why.split()) >= 5, m


def test_the_scan_sees_the_modules_it_must():
    reg = registered()
    assert {"moy_ui", "moy_input", "moy_axs", "moy_lcd", "moy_ppa"} <= set(reg)
    assert _generated() & set(reg), "no generated module found: the face loader moved"

"""The kernel's portable C (native/moy_kernel): the crash record, the boot
decision and the recovery screen's raster, on the host through ctypes.

The board half -- the panic wrapper, the intake, the VM service, the floor's
input -- runs only on glass (tools/kernel_gate.py). What runs here is what that
half decides with and draws with, so a board and this file agree by
construction: the floor prints its lines and its framebuffer's crc32, and
`render` below reproduces the crc from the lines alone.
"""

import ctypes
import os
import zlib

import pytest

from runtime import native_build
from runtime.crash_guard import CrashGuard

ROOT = native_build.ROOT
KERNEL = os.path.join(ROOT, "native", "moy_kernel")
GFX = os.path.join(ROOT, "native", "moy_gfx")
LIBMOY = os.path.join(GFX, "libmoy")

u8, u16, u32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32

# moy_crash.h's enums.
CRASH_FAULT, CRASH_ABORT, CRASH_VM = 1, 2, 7
ROLE_APP, ROLE_WALLPAPER, ROLE_GAME = 1, 2, 3
BOOT_START, BOOT_SAFE, BOOT_REPL, BOOT_RECOVERY = 1, 2, 3, 4
WHY_VM_START, WHY_BOOT_LOOP, WHY_HEAP = 1, 2, 3
TEST_VM_START, TEST_HEAP = 1, 2
BOOT_LOOP_STARTS = 4

# Each console's floor: (logical w, h, rotation, byte-swapped framebuffer),
# what its mpconfigboard.h declares, and whether a finger picks on it.
CONSOLES = {
    "tdeck": (320, 240, 0, 1),
    "guition_s3": (480, 320, 0, 1),
    "p4": (1024, 600, 0, 0),
    "guition_p4": (1280, 800, 270, 0),
}
TOUCH = {"guition_s3"}
# Each floor's foot and whether the serial words get a line of their own,
# as moy_kernel.c's moy_kernel_recovery makes them from the board's input:
# a button (MOY_KERNEL_BUTTON_NAME), a touch panel, or nothing but serial and
# the idle countdown to SAFE.
FOOT = {
    "tdeck": ("Press the trackball for the next, hold it to start.", 1),
    "guition_s3": ("Tap a choice, or type retry, safe or repl on serial.", 0),
    "p4": ("Press BOOT for the next, hold it to start.", 1),
    "guition_p4": ("Safe start in 30 s, or type retry, safe or repl on serial.", 0),
}
# moy_kernel.c's K_BAND_ROWS and K_SCRATCH.
BAND_ROWS = 8
SCRATCH = 12288


class Rec(ctypes.Structure):
    _fields_ = [("magic", u32), ("version", u16), ("size", u16), ("crc", u32),
                ("boot", u32), ("uptime_ms", u32), ("build", u32), ("pc", u32),
                ("cause", u32), ("addr", u32), ("bt", u32 * 4), ("kind", u8),
                ("core", u8), ("role", u8), ("reset", u8),
                ("task", ctypes.c_char * 16), ("id", ctypes.c_char * 24),
                ("what", ctypes.c_char * 32)]


class KState(ctypes.Structure):
    _fields_ = [("magic", u32), ("boot", u32), ("unproven", u8), ("next", u8),
                ("reason", u8), ("test", u8), ("open_app", ctypes.c_char * 24),
                ("open_wallpaper", ctypes.c_char * 24), ("open_game", ctypes.c_char * 24)]


class Decision(ctypes.Structure):
    _fields_ = [("action", u8), ("reason", u8), ("test", u8)]


class Geom(ctypes.Structure):
    _fields_ = [(n, ctypes.c_int) for n in ("w", "h", "fb_w", "fb_h", "rot", "swap", "touch")] \
        + [("band", ctypes.c_void_p), ("band_rows", ctypes.c_int),
           ("scratch", ctypes.c_void_p), ("scratch_n", ctypes.c_size_t)]


COLS = 64


class View(ctypes.Structure):
    _fields_ = [("line", (ctypes.c_char * COLS) * 8), ("nlines", ctypes.c_int),
                ("hint", ctypes.c_char * COLS), ("sel", ctypes.c_int)]


def _lib():
    so = native_build.build(
        "moy_kernel_host", os.path.join(KERNEL, "moy_recovery.c"),
        ["moy_crash.c", "moy_crash.h", "moy_recovery.h", "moy_boot.c", "moy_boot.h",
         "moy_scr.c", "moy_scr.h", "moy_path.c", "moy_path.h", "moy_path_faces.h",
         "moy_str.h", "moy_str.c", "moy_str_table.h", "moy_str_table.c"],
        os.path.join(ROOT, ".build", "host_kernel"),
        cflags=native_build.BASE_CFLAGS + ["-Wall", "-Werror"],
        libmoy_dir=[KERNEL, GFX, LIBMOY, os.path.join(ROOT, "native", "moy_spine")])
    if so is None:
        pytest.skip("no C compiler")
    lib = ctypes.CDLL(so)
    lib.moy_crash_crc32.restype = u32
    lib.moy_crash_crc32.argtypes = [u32, ctypes.c_void_p, ctypes.c_size_t]
    lib.moy_boot_decide.restype = Decision
    lib.moy_kstate_open_id.argtypes = [ctypes.POINTER(KState), ctypes.POINTER(ctypes.c_char_p)]
    lib.moy_recovery_hit.argtypes = [ctypes.POINTER(Geom), ctypes.c_int, ctypes.c_int]
    lib.moy_recovery_view.argtypes = [ctypes.POINTER(View), ctypes.c_int, ctypes.POINTER(Rec),
                                      ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.moy_web_screen_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(Geom),
                                          ctypes.POINTER(View), ctypes.c_void_p]
    lib.moy_boot_logo_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(Geom), ctypes.c_void_p]
    return lib


@pytest.fixture(scope="module")
def lib():
    return _lib()


def test_the_kernel_builds_without_the_raster():
    """The headless Zero denies moy_gfx, so its kernel compiles with no
    moy_scr.h to include: the floor's words and its decision stay, the
    screens are left out."""
    so = native_build.build(
        "moy_kernel_headless", os.path.join(KERNEL, "moy_recovery.c"),
        ["moy_crash.c", "moy_crash.h", "moy_recovery.h", "moy_boot.c", "moy_boot.h",
         "moy_str.h", "moy_str.c", "moy_str_table.h", "moy_str_table.c"],
        os.path.join(ROOT, ".build", "host_kernel_headless"),
        cflags=native_build.BASE_CFLAGS + ["-Wall", "-Werror"],
        libmoy_dir=[KERNEL, os.path.join(ROOT, "native", "moy_spine")])
    if so is None:
        pytest.skip("no C compiler")
    headless = ctypes.CDLL(so)
    assert hasattr(headless, "moy_recovery_view") and hasattr(headless, "moy_boot_decide")
    assert not hasattr(headless, "moy_recovery_render")
    assert not hasattr(headless, "moy_boot_logo_render")


def _record(**kw):
    r = Rec()
    r.kind, r.core, r.role = CRASH_FAULT, 1, ROLE_APP
    r.pc, r.cause, r.addr = 0x42012345, 29, 0
    r.boot, r.uptime_ms, r.reset = 7, 81234, 4
    r.task, r.id, r.what = b"mp_task", b"paint", b"StoreProhibited"
    for k, v in kw.items():
        setattr(r, k, v)
    return r


# ---- the record -----------------------------------------------------------------

def test_the_record_is_128_bytes_and_its_crc_is_zlibs(lib):
    assert ctypes.sizeof(Rec) == 128 and ctypes.sizeof(KState) == 84
    r = _record()
    lib.moy_crash_seal(ctypes.byref(r))
    assert (r.magic, r.version, r.size) == (0x4D4F5943, 1, 128)
    assert r.crc == zlib.crc32(bytes(r)[12:])
    assert lib.moy_crash_valid(ctypes.byref(r))
    assert lib.moy_crash_crc32(0, b"123456789", 9) == 0xCBF43926


def test_a_torn_or_foreign_record_reads_as_none(lib):
    """Power-on garbage, a wrapper that died half-way, a record from another
    layout: each is ABSENT, never a crash with made-up fields."""
    assert not lib.moy_crash_valid(ctypes.byref(Rec()))
    r = _record()
    lib.moy_crash_seal(ctypes.byref(r))
    for field, value in (("pc", 1), ("kind", 0), ("version", 2), ("magic", 0)):
        t = Rec.from_buffer_copy(bytes(r))
        setattr(t, field, value)
        assert not lib.moy_crash_valid(ctypes.byref(t)), field
    raw = bytearray(bytes(r))
    raw[100] ^= 0x40
    assert not lib.moy_crash_valid(ctypes.byref(Rec.from_buffer_copy(bytes(raw))))


def test_a_copied_string_is_terminated_and_cleared(lib):
    buf = (ctypes.c_char * 8)(*b"XXXXXXXX")
    lib.moy_crash_strcpy(buf, 8, b"a long task name")
    assert bytes(buf) == b"a long \0"
    lib.moy_crash_strcpy(buf, 8, None)
    assert bytes(buf) == b"\0" * 8


# ---- the boot decision ------------------------------------------------------------

def _state(lib):
    st = KState()
    st.magic = 0xDEADBEEF                       # power-on garbage
    st.unproven, st.next = 200, 9
    lib.moy_kstate_open(ctypes.byref(st))
    return st


def _decide(lib, st):
    d = lib.moy_boot_decide(ctypes.byref(st))
    return d.action, d.reason, d.test


def test_a_power_on_starts_the_count_over(lib):
    st = _state(lib)
    assert (st.magic, st.boot, st.unproven, st.next) == (0x4D4F594B, 1, 0, 0)
    lib.moy_kstate_open(ctypes.byref(st))
    assert st.boot == 2


def test_a_boot_forgets_the_last_boots_open_ids(lib):
    """The OPEN ids name what the last boot's VM was running, which the record
    (if that VM died) already holds. A reboot that kept them would blame the
    next crash on an app the new boot never opened."""
    st = _state(lib)
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_APP, b"paint")
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_WALLPAPER, b"aurora")
    lib.moy_kstate_open(ctypes.byref(st))
    assert (st.open_app, st.open_wallpaper, st.boot) == (b"", b"", 2)


def test_starts_without_a_first_frame_send_the_next_boot_to_the_floor(lib):
    st = _state(lib)
    for n in range(1, BOOT_LOOP_STARTS + 1):
        assert _decide(lib, st) == (BOOT_START, 0, 0)
        assert st.unproven == n
    assert _decide(lib, st) == (BOOT_RECOVERY, WHY_BOOT_LOOP, 0)
    assert _decide(lib, st) == (BOOT_RECOVERY, WHY_BOOT_LOOP, 0)
    st.next = BOOT_START                         # RETRY on the floor
    assert _decide(lib, st) == (BOOT_START, 0, 0)
    assert st.unproven == 1
    lib.moy_boot_proven(ctypes.byref(st))
    assert st.unproven == 0


def test_the_wallpaper_ledger_recovers_before_the_boot_loop_guard_trips(lib):
    """A wallpaper that kills the board is the boot loop #160's ledger was
    built for: three crashing boots strike it out, and the fourth boots
    without it. The kernel's guard must let that fourth boot START, or it
    would take the ledger's recovery away and put a kid on the floor."""
    assert BOOT_LOOP_STARTS > CrashGuard.STRIKES
    st = _state(lib)
    for _ in range(CrashGuard.STRIKES + 1):
        assert _decide(lib, st)[0] == BOOT_START


def test_each_choice_on_the_floor_is_the_next_boot_once(lib):
    st = _state(lib)
    st.unproven = 9
    st.next = BOOT_SAFE
    assert _decide(lib, st) == (BOOT_SAFE, 0, 0) and st.unproven == 1
    st.next = BOOT_REPL
    assert _decide(lib, st) == (BOOT_REPL, 0, 0) and st.unproven == 0
    assert _decide(lib, st) == (BOOT_START, 0, 0)


def test_a_failed_start_arms_the_floor_with_its_reason(lib):
    st = _state(lib)
    st.next, st.reason = BOOT_RECOVERY, WHY_VM_START
    assert _decide(lib, st) == (BOOT_RECOVERY, WHY_VM_START, 0)
    assert (st.next, st.reason) == (0, 0)
    assert _decide(lib, st)[0] == BOOT_START


def test_a_dev_test_flag_rides_one_start_and_never_the_floor(lib):
    st = _state(lib)
    st.test = TEST_VM_START
    assert _decide(lib, st) == (BOOT_START, 0, TEST_VM_START)
    assert _decide(lib, st) == (BOOT_START, 0, 0)
    st.test, st.next, st.reason = TEST_HEAP, BOOT_RECOVERY, WHY_HEAP
    assert _decide(lib, st) == (BOOT_RECOVERY, WHY_HEAP, 0)
    assert st.test == 0


def test_a_crash_names_the_app_before_the_wallpaper(lib):
    st = _state(lib)
    out = ctypes.c_char_p()
    assert lib.moy_kstate_open_id(ctypes.byref(st), ctypes.byref(out)) == 0
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_WALLPAPER, b"aurora")
    assert lib.moy_kstate_open_id(ctypes.byref(st), ctypes.byref(out)) == ROLE_WALLPAPER
    assert out.value == b"aurora"
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_APP, b"a-much-longer-app-id-than-fits")
    assert lib.moy_kstate_open_id(ctypes.byref(st), ctypes.byref(out)) == ROLE_APP
    assert out.value == b"a-much-longer-app-id-th"
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_APP, None)
    assert lib.moy_kstate_open_id(ctypes.byref(st), ctypes.byref(out)) == ROLE_WALLPAPER


def test_a_crash_names_a_game_between_the_app_and_the_wallpaper(lib):
    st = _state(lib)
    out = ctypes.c_char_p()
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_WALLPAPER, b"aurora")
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_GAME, b"moybyte.brick_siege_lua")
    assert lib.moy_kstate_open_id(ctypes.byref(st), ctypes.byref(out)) == ROLE_GAME
    assert out.value == b"moybyte.brick_siege_lua"
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_APP, b"paint")
    assert lib.moy_kstate_open_id(ctypes.byref(st), ctypes.byref(out)) == ROLE_APP
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_APP, None)
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_GAME, b"")
    assert lib.moy_kstate_open_id(ctypes.byref(st), ctypes.byref(out)) == ROLE_WALLPAPER
    # the next boot's state carries no OPEN id of the last boot's
    lib.moy_kstate_arm(ctypes.byref(st), ROLE_GAME, b"sky_run")
    lib.moy_kstate_open(ctypes.byref(st))
    assert st.open_game == b""


# ---- the recovery screen ----------------------------------------------------------

_MEM = {}


def geom(lib, w, h, rot, swap, touch=0):
    """The geometry the kernel's k_geom makes: a turned panel's band and the
    faces' scratch, the kernel's PSRAM block there and kept alive here."""
    g = Geom()
    lib.moy_rgeom_init(ctypes.byref(g), w, h, rot, swap)
    g.touch = touch
    key = (w, rot)
    if key not in _MEM:
        _MEM[key] = ((u16 * (w * BAND_ROWS))(), (ctypes.c_uint64 * (SCRATCH // 8))())
    band, scratch = _MEM[key]
    if rot:
        g.band, g.band_rows = ctypes.addressof(band), BAND_ROWS
    g.scratch, g.scratch_n = ctypes.addressof(scratch), SCRATCH
    return g


def board_geom(lib, board):
    return geom(lib, *CONSOLES[board], touch=int(board in TOUCH))


def render(lib, board, lines, hint, sel):
    """(crc32, words) of the floor `board` draws for these lines: what the
    board prints as `KERNEL recovery ... crc=` beside its `KERNEL line`s."""
    g = board_geom(lib, board)
    v = View()
    for i, text in enumerate(lines):
        v.line[i].value = text.encode()[:COLS - 1]
    v.nlines, v.hint, v.sel = len(lines), hint.encode()[:COLS - 1], sel
    fb = (ctypes.c_uint16 * (g.fb_w * g.fb_h))()
    assert lib.moy_recovery_render(fb, ctypes.byref(g), ctypes.byref(v)) == 0
    return zlib.crc32(bytes(fb)), fb


def _view(lib, rec=None, serial=0, hint="Type retry, safe or repl on serial."):
    v = View()
    lib.moy_recovery_view(ctypes.byref(v), WHY_VM_START,
                          ctypes.byref(rec) if rec is not None else None,
                          b"1.2.3-test", hint.encode(), serial)
    return v


def test_the_screen_says_why_what_and_which_firmware(lib):
    v = _view(lib, _record())
    lines = [v.line[i].value.decode() for i in range(v.nlines)]
    assert lines == ["The console did not start.", "Last: fault in mp_task",
                     "app: paint", "StoreProhibited", "PC 42012345 at 00000000",
                     "Up 81 s, boot 7", "Firmware 1.2.3-test"]
    v = _view(lib)
    assert [v.line[i].value for i in range(v.nlines)] == [
        b"The console did not start.", b"Firmware 1.2.3-test"]
    v = _view(lib, serial=1)
    assert v.line[v.nlines - 1].value == b"Serial: retry, safe or repl"


def test_the_serial_protocol_keeps_its_words(lib):
    """tools/kernel_gate.py reads `sel=RETRY|SAFE|REPL` whatever the rows say."""
    lib.moy_recovery_choice_name.restype = ctypes.c_char_p
    assert [lib.moy_recovery_choice_name(i) for i in range(3)] == [b"RETRY", b"SAFE", b"REPL"]


# The crc32 of each console's floor for _view(_record()) with its FOOT and
# RETRY selected;
# MOY_RECOVERY_PNG=<dir> writes each as a PNG to look at.
GOLDEN = {
    "tdeck": 0x8302ECBC,
    "guition_s3": 0xF0A214D7,
    "p4": 0x8ED2C4F7,
    "guition_p4": 0x6FB95981,
}


@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_each_consoles_floor_is_pinned(lib, board):
    hint, serial = FOOT[board]
    v = _view(lib, _record(), serial=serial, hint=hint)
    lines = [v.line[i].value.decode() for i in range(v.nlines)]
    crc, fb = render(lib, board, lines, v.hint.decode(), 0)
    png = os.environ.get("MOY_RECOVERY_PNG")
    if png:
        _write_png(os.path.join(png, "%s.png" % board), fb, *CONSOLES[board])
    assert crc == GOLDEN[board], "%s: %08x" % (board, crc)


def test_every_printable_byte_draws_in_the_kernels_scratch(lib):
    """The floor draws a crash record's own text, any printable byte, through
    a scratch it never grows: every glyph at the largest size a kernel screen
    draws in fits it, on the widest panel."""
    src = open(os.path.join(KERNEL, "moy_kernel.c")).read()
    assert "#define K_SCRATCH %d\n" % SCRATCH in src
    assert "#define K_BAND_ROWS %d\n" % BAND_ROWS in src
    g = geom(lib, 1280, 800, 0, 0)
    v = View()
    text = bytes(range(0x20, 0x7F))
    for i in range(0, len(text), 40):
        v.line[i // 40 + 1].value = text[i:i + 40]
    v.line[0].value, v.nlines = b"x", len(text) // 40 + 2
    v.hint = text[:60]
    fb = (u16 * (1280 * 800))()
    assert lib.moy_recovery_render(fb, ctypes.byref(g), ctypes.byref(v)) == 0
    for line in (text[i:i + 12] for i in range(0, len(text), 12)):
        w = View()
        lib.moy_web_screen_view(ctypes.byref(w), b"http://" + b"1" * 3 + b"/?pin=" + line)
        assert lib.moy_web_screen_render(fb, ctypes.byref(g), ctypes.byref(w), None) == 0


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_a_rotation_is_the_ppa_s_counter_clockwise_turn(lib, rot):
    """The rotated compositor's convention (moy_rot_rect), pixel for pixel: 90 maps the
    logical (x, y) to (y, w-1-x), 270 to (h-1-y, x), 180 to (w-1-x, h-1-y).
    A turned screen draws in bands, and the bands are the unturned picture."""
    w, h = 320, 200
    lines = ["Rotate %d" % rot]
    flat = _render_geom(lib, w, h, 0, lines)
    turned = _render_geom(lib, w, h, rot, lines)
    fw = h if rot in (90, 270) else w
    for (x, y) in ((0, 0), (5, 17), (w - 1, 0), (0, h - 1), (123, 45), (w - 1, h - 1)):
        fx, fy = {90: (y, w - 1 - x), 180: (w - 1 - x, h - 1 - y),
                  270: (h - 1 - y, x)}[rot]
        assert turned[fy * fw + fx] == flat[y * w + x], (x, y)
    assert sorted(turned) == sorted(flat)


def _render_geom(lib, w, h, rot, lines, swap=0):
    g = geom(lib, w, h, rot, swap)
    v = View()
    for i, text in enumerate(lines):
        v.line[i].value = text.encode()
    v.nlines = len(lines)
    fb = (ctypes.c_uint16 * (g.fb_w * g.fb_h))()
    assert lib.moy_recovery_render(fb, ctypes.byref(g), ctypes.byref(v)) == 0
    return list(fb)


def test_a_turned_screen_without_a_band_draws_nothing(lib):
    g = geom(lib, 320, 200, 90, 0)
    g.band = None
    fb = (ctypes.c_uint16 * (320 * 200))()
    assert lib.moy_recovery_render(fb, ctypes.byref(g), ctypes.byref(View())) != 0
    assert not any(fb)


def test_a_swapped_framebuffer_is_the_same_picture_high_byte_first(lib):
    plain = _render_geom(lib, 320, 240, 0, ["Swap"])
    swapped = _render_geom(lib, 320, 240, 0, ["Swap"], swap=1)
    assert [((p >> 8) | (p << 8)) & 0xFFFF for p in swapped] == plain


@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_a_tap_on_a_choice_is_that_choice(lib, board):
    """A row of the choices' card is its choice, the whole row; above the card,
    beside it and under it nothing is."""
    g = board_geom(lib, board)
    w, h = CONSOLES[board][:2]
    hits = {}
    for y in range(h):
        got = lib.moy_recovery_hit(ctypes.byref(g), w // 2, y)
        if got >= 0:
            hits.setdefault(got, []).append(y)
    assert sorted(hits) == [0, 1, 2]
    spans = [(min(v), max(v)) for _k, v in sorted(hits.items())]
    assert spans[0][1] + 1 == spans[1][0] and spans[1][1] + 1 == spans[2][0]
    rh = spans[0][1] - spans[0][0] + 1
    assert rh >= (28 if board in TOUCH else 22)
    assert lib.moy_recovery_hit(ctypes.byref(g), 2, spans[1][0]) == -1


def _write_png(path, fb, w, h, rot, swap):
    import struct
    fw, fh = (h, w) if rot in (90, 270) else (w, h)
    rows = []
    for y in range(fh):
        row = bytearray(b"\0")
        for x in range(fw):
            p = fb[y * fw + x]
            if swap:
                p = ((p >> 8) | (p << 8)) & 0xFFFF
            row += bytes((((p >> 11) & 31) * 255 // 31, ((p >> 5) & 63) * 255 // 63,
                          (p & 31) * 255 // 31))
        rows.append(bytes(row))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", fw, fh, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


# ---- the plain web-console screen (no VM) ----------------------------------------

def _web(lib, board, url, pal=None):
    g = board_geom(lib, board)
    v = View()
    lib.moy_web_screen_view(ctypes.byref(v), url)
    fb = (ctypes.c_uint16 * (g.fb_w * g.fb_h))()
    assert lib.moy_web_screen_render(fb, ctypes.byref(g), ctypes.byref(v), pal) == 0
    return v, zlib.crc32(bytes(fb)), fb


@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_the_plain_web_screen_says_where_to_go(lib, board):
    """What the kernel draws while no VM runs and the web console serves
    (docs/kernel_survival_2026-10.md section 13, answer 8; docs/look_2026-10.md
    step 33): the address to open and the pin, the shell's page without its
    QR -- and the address is what the pixels carry. With no address, that the
    console is restarting."""
    v, crc, fb = _web(lib, board, b"http://192.168.1.7/?pin=1234")
    lines = [v.line[i].value.decode() for i in range(v.nlines)]
    assert lines == ["Open this in a browser:", "192.168.1.7", "1234"]
    png = os.environ.get("MOY_RECOVERY_PNG")
    if png:
        _write_png(os.path.join(png, "web-%s.png" % board), fb, *CONSOLES[board])
    _v, other, _fb = _web(lib, board, b"http://10.0.0.2/?pin=9999")
    assert other != crc
    v, waiting, fb = _web(lib, board, b"")
    assert [v.line[i].value for i in range(v.nlines)] == [b"The console is restarting..."]
    assert waiting not in (crc, other)
    if png:
        _write_png(os.path.join(png, "web-restarting-%s.png" % board), fb, *CONSOLES[board])


def test_the_web_screen_keeps_a_port_and_draws_on_the_kept_ground(lib):
    v, day, _fb = _web(lib, "tdeck", b"http://192.168.1.7:8080/?pin=42")
    assert [v.line[i].value for i in range(3)] == [b"Open this in a browser:",
                                                   b"192.168.1.7:8080", b"42"]
    night = (u16 * 6)(0x0884, 0xEF9F, 0xA539, 0xB55E, 0x31CB, 0x31CB)
    _v, kept, fb = _web(lib, "tdeck", b"http://192.168.1.7:8080/?pin=42", night)
    assert kept != day and fb[0] == 0x8408       # Night's ground, high byte first


# ---- the first light ----------------------------------------------------------

@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_the_kernels_logo_is_the_boot_screens_picture(lib, board):
    """The kernel lights the glass with the logo before any VM (moy_boot.c),
    and the themed boot screen follows: the two are one picture, or the
    machine appears to start twice. The C raster against console.draw_splash
    on the host canvas, word for word, on every console's logical screen."""
    from runtime import console, host_canvas
    w, h = CONSOLES[board][:2]
    g = geom(lib, w, h, 0, 0)
    fb = (u16 * (w * h))()
    assert lib.moy_boot_logo_render(fb, ctypes.byref(g), None) == 0
    cv = host_canvas.make_system_canvas(w, h)
    console.draw_splash(cv)
    cv.flush_batch()
    import device_canvas
    words = memoryview(cv._buf).cast("H")
    if device_canvas.PAL565_WIRE is not device_canvas.PAL565:
        words = [((v >> 8) | (v << 8)) & 0xFFFF for v in words]
    want = list(words)
    got = list(fb)
    assert len(set(got)) >= 5
    bad = [i for i in range(w * h) if got[i] != want[i]]
    assert not bad, "%d pixels differ, first at (%d, %d): C %04x, Python %04x" % (
        len(bad), bad[0] % w, bad[0] // w, got[bad[0]], want[bad[0]])


def test_the_kernels_palettes_are_moys(lib):
    """The kernel cannot read a theme, so moy_scr.c and moy_recovery.c carry
    Moy Day's splash palette and Moy Night's recovery roles as constants:
    held here to the theme they copy."""
    from runtime import chrome, console
    day = (u16 * 6).in_dll(lib, "moy_splash_day")
    assert bytes(day) == console.splash_pal(chrome.theme_colors("moy", "light"))
    th = chrome.theme_colors("moy", "dark")
    night = [chrome.rgb565_of(th[k]) for k in ("panel", "surface", "ink", "ink_dim",
                                               "border", "accent")]
    src = open(os.path.join(KERNEL, "moy_recovery.c")).read()
    assert "static const uint16_t NIGHT[N_PAL] = {%s};" % ", ".join(
        "0x%04Xu" % c for c in night) in src


def test_moys_art_is_chromes(lib):
    """moy_scr.c draws Moy from its own copy of chrome.py's "moy" art."""
    from runtime import chrome
    from device_canvas import PAL565
    g = geom(lib, 16, 16, 0, 0)
    fb = (u16 * 256)()
    v = View()
    v.nlines = 0
    lib.moy_scr_moy.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    s = _Scr()
    s.fb, s.fb_w, s.w, s.h = ctypes.addressof(fb), 16, 16, 16
    s.px, s.stride, s.y0, s.y1 = ctypes.addressof(fb), 16, 0, 16
    for i in range(256):
        fb[i] = 0x1234
    lib.moy_scr_moy(ctypes.byref(s), 0, 0, 1)
    art = chrome._ICON_ART["moy"]
    for y in range(16):
        for x in range(16):
            ch = art[y][x] if x < len(art[y]) else "."
            want = 0x1234 if ch == "." else PAL565[int(ch, 16)]
            assert fb[y * 16 + x] == want, (x, y)


class _Scr(ctypes.Structure):
    _fields_ = [("fb", ctypes.c_void_p), ("fb_w", ctypes.c_int), ("w", ctypes.c_int),
                ("h", ctypes.c_int), ("rot", ctypes.c_int), ("swap", ctypes.c_int),
                ("band", ctypes.c_void_p), ("band_rows", ctypes.c_int),
                ("scratch", ctypes.c_void_p), ("scratch_n", ctypes.c_size_t),
                ("px", ctypes.c_void_p), ("stride", ctypes.c_int), ("y0", ctypes.c_int),
                ("y1", ctypes.c_int), ("fails", ctypes.c_int)]


def test_each_floor_names_its_input_once():
    """The foot names the board's own way to choose, and the serial words
    show once: in the foot where it has room for them, else on their own
    line -- never both. FOOT is moy_kernel.c's, string for string."""
    import json
    en = json.load(open(os.path.join(ROOT, "native", "moy_spine", "strings", "en.json")))
    en = en.get("strings", en)
    src = open(os.path.join(KERNEL, "moy_kernel.c")).read()
    assert "int serial = 1;" in src and "MOY_S_RECOVERY_HINT_BUTTON" in src
    buttons = {"tdeck": "the trackball", "p4": "BOOT"}
    for board, d in (("tdeck", "lilygo_t_deck_plus_mainline/boards/MOYBYTE_TDECK"),
                     ("p4", "esp32_p4_wifi6_touch_lcd_7b/boards/MOYBYTE_P4")):
        h = open(os.path.join(ROOT, "firmware", d, "mpconfigboard.h")).read()
        assert '#define MOY_KERNEL_BUTTON_NAME              "%s"' % buttons[board] in h
    for board, (hint, serial) in FOOT.items():
        if board in buttons:
            assert hint == en["recovery.hint_button"] % buttons[board] and serial
        elif board in TOUCH:
            assert hint == en["recovery.hint_touch"] and not serial
        else:
            assert hint == en["recovery.safe_in"] % 30 and not serial
        assert ("retry, safe or repl" in hint) != bool(serial), board

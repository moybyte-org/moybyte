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
LIBMOY = os.path.join(ROOT, "native", "moy_gfx", "libmoy")

u8, u16, u32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32

# moy_crash.h's enums.
CRASH_FAULT, CRASH_ABORT, CRASH_VM = 1, 2, 7
ROLE_APP, ROLE_WALLPAPER, ROLE_GAME = 1, 2, 3
BOOT_START, BOOT_SAFE, BOOT_REPL, BOOT_RECOVERY = 1, 2, 3, 4
WHY_VM_START, WHY_BOOT_LOOP, WHY_HEAP = 1, 2, 3
TEST_VM_START, TEST_HEAP = 1, 2
BOOT_LOOP_STARTS = 4

# Each console's floor: (logical w, h, rotation, byte-swapped framebuffer),
# what its mpconfigboard.h declares.
CONSOLES = {
    "tdeck": (320, 240, 0, 1),
    "guition_s3": (480, 320, 0, 1),
    "p4": (1024, 600, 0, 0),
    "guition_p4": (1280, 800, 270, 0),
}


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
    _fields_ = [(n, ctypes.c_int) for n in ("w", "h", "fb_w", "fb_h", "rot", "swap", "scale")]


class View(ctypes.Structure):
    _fields_ = [("line", (ctypes.c_char * 40) * 8), ("nlines", ctypes.c_int),
                ("hint", ctypes.c_char * 40), ("sel", ctypes.c_int)]


def _lib():
    so = native_build.build(
        "moy_kernel_host", os.path.join(KERNEL, "moy_recovery.c"),
        ["moy_crash.c", "moy_crash.h", "moy_recovery.h", "moy_boot.c", "moy_boot.h",
         "moy_data.c", "moy.h"],
        os.path.join(ROOT, ".build", "host_kernel"),
        cflags=native_build.BASE_CFLAGS + ["-Wall", "-Werror"],
        libmoy_dir=[KERNEL, LIBMOY])
    if so is None:
        pytest.skip("no C compiler")
    lib = ctypes.CDLL(so)
    lib.moy_crash_crc32.restype = u32
    lib.moy_crash_crc32.argtypes = [u32, ctypes.c_void_p, ctypes.c_size_t]
    lib.moy_boot_decide.restype = Decision
    lib.moy_kstate_open_id.argtypes = [ctypes.POINTER(KState), ctypes.POINTER(ctypes.c_char_p)]
    lib.moy_recovery_hit.argtypes = [ctypes.POINTER(Geom), ctypes.c_int, ctypes.c_int]
    lib.moy_recovery_view.argtypes = [ctypes.POINTER(View), ctypes.c_int, ctypes.POINTER(Rec),
                                      ctypes.c_char_p, ctypes.c_char_p]
    return lib


@pytest.fixture(scope="module")
def lib():
    return _lib()


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

def geom(lib, w, h, rot, swap):
    g = Geom()
    lib.moy_rgeom_init(ctypes.byref(g), w, h, rot, swap)
    return g


def render(lib, board, lines, hint, sel):
    """(crc32, words) of the floor `board` draws for these lines: what the
    board prints as `KERNEL recovery ... crc=` beside its `KERNEL line`s."""
    g = geom(lib, *CONSOLES[board])
    v = View()
    for i, text in enumerate(lines):
        v.line[i].value = text.encode()[:39]
    v.nlines, v.hint, v.sel = len(lines), hint.encode()[:39], sel
    fb = (ctypes.c_uint16 * (g.fb_w * g.fb_h))()
    lib.moy_recovery_render(fb, ctypes.byref(g), ctypes.byref(v))
    return zlib.crc32(bytes(fb)), fb


def _view(lib, rec=None):
    v = View()
    lib.moy_recovery_view(ctypes.byref(v), WHY_VM_START,
                          ctypes.byref(rec) if rec is not None else None,
                          b"1.2.3-test", b"SERIAL: retry / safe / repl")
    return v


def test_the_screen_says_why_what_and_which_firmware(lib):
    v = _view(lib, _record())
    lines = [v.line[i].value.decode() for i in range(v.nlines)]
    assert lines == ["THE CONSOLE DID NOT START", "LAST: fault in mp_task",
                     "app: paint", "StoreProhibited", "PC 42012345 AT 00000000",
                     "UP 81s  BOOT 7", "FW 1.2.3-test"]
    v = _view(lib)
    assert [v.line[i].value for i in range(v.nlines)] == [
        b"THE CONSOLE DID NOT START", b"FW 1.2.3-test"]


# The crc32 of each console's floor for _view(_record()) with RETRY selected;
# MOY_RECOVERY_PNG=<dir> writes each as a PNG to look at.
GOLDEN = {
    "tdeck": 0xDFA75B5B,
    "guition_s3": 0x67CED567,
    "p4": 0x49BDB23D,
    "guition_p4": 0x0F75EB65,
}


@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_each_consoles_floor_is_pinned(lib, board):
    v = _view(lib, _record())
    lines = [v.line[i].value.decode() for i in range(v.nlines)]
    crc, fb = render(lib, board, lines, v.hint.decode(), 0)
    png = os.environ.get("MOY_RECOVERY_PNG")
    if png:
        _write_png(os.path.join(png, "%s.png" % board), fb, *CONSOLES[board])
    assert crc == GOLDEN[board], "%s: %08x" % (board, crc)


@pytest.mark.parametrize("rot", [90, 180, 270])
def test_a_rotation_is_the_ppa_s_counter_clockwise_turn(lib, rot):
    """The rotated compositor's convention (moy_rot_rect), pixel for pixel: 90 maps the
    logical (x, y) to (y, w-1-x), 270 to (h-1-y, x), 180 to (w-1-x, h-1-y)."""
    w, h = 320, 200
    lines = ["ROTATE %d" % rot]
    flat = _render_geom(lib, w, h, 0, lines)
    turned = _render_geom(lib, w, h, rot, lines)
    fw = h if rot in (90, 270) else w
    for (x, y) in ((0, 0), (5, 17), (w - 1, 0), (0, h - 1), (123, 45), (w - 1, h - 1)):
        fx, fy = {90: (y, w - 1 - x), 180: (w - 1 - x, h - 1 - y),
                  270: (h - 1 - y, x)}[rot]
        assert turned[fy * fw + fx] == flat[y * w + x], (x, y)
    assert sorted(turned) == sorted(flat)


def _render_geom(lib, w, h, rot, lines):
    g = geom(lib, w, h, rot, 0)
    v = View()
    for i, text in enumerate(lines):
        v.line[i].value = text.encode()
    v.nlines = len(lines)
    fb = (ctypes.c_uint16 * (g.fb_w * g.fb_h))()
    lib.moy_recovery_render(fb, ctypes.byref(g), ctypes.byref(v))
    return list(fb)


def test_a_swapped_framebuffer_is_the_same_picture_high_byte_first(lib):
    plain = _render_geom(lib, 320, 240, 0, ["SWAP"])
    g = geom(lib, 320, 240, 0, 1)
    v = View()
    v.line[0].value, v.nlines = b"SWAP", 1
    fb = (ctypes.c_uint16 * (320 * 240))()
    lib.moy_recovery_render(fb, ctypes.byref(g), ctypes.byref(v))
    assert [((p >> 8) | (p << 8)) & 0xFFFF for p in fb] == plain


@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_a_tap_on_a_choice_is_that_choice(lib, board):
    w, h, rot, swap = CONSOLES[board]
    g = geom(lib, w, h, rot, swap)
    c = 8 * g.scale
    bw = (w - 4 * c) // 3
    y = h - 5 * c + c
    for i in range(3):
        assert lib.moy_recovery_hit(ctypes.byref(g), c + i * (bw + c) + bw // 2, y) == i
    assert lib.moy_recovery_hit(ctypes.byref(g), w // 2, c) == -1
    assert lib.moy_recovery_hit(ctypes.byref(g), c // 2, y) == -1


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

def _plain(lib, board, url):
    g = geom(lib, *CONSOLES[board])
    v = View()
    lib.moy_web_screen_view(ctypes.byref(v), url, None, b"1.2.3-test")
    fb = (ctypes.c_uint16 * (g.fb_w * g.fb_h))()
    lib.moy_recovery_render_plain(fb, ctypes.byref(g), b"MOYBYTE WEB CONSOLE",
                                  ctypes.byref(v))
    return v, zlib.crc32(bytes(fb))


@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_the_plain_web_screen_says_where_to_go(lib, board):
    """What the kernel draws while no VM runs and the web console serves
    (docs/kernel_survival_2026-10.md section 13, answer 8): the address,
    the firmware, no choices -- and the address is what the pixels carry."""
    v, crc = _plain(lib, board, b"http://192.168.1.7/?pin=1234")
    lines = [v.line[i].value.decode() for i in range(v.nlines)]
    assert lines == ["OPEN THIS IN A BROWSER:", "http://192.168.1.7/?pin=1234",
                     "FW 1.2.3-test"]
    assert v.hint == b"THE CONSOLE IS RESTARTING"
    _v, other = _plain(lib, board, b"http://10.0.0.2/?pin=9999")
    assert other != crc
    floor, _fb = render(lib, board, lines, v.hint.decode(), 0)
    assert floor != crc, "the plain screen draws no choices"


# ---- the first light ----------------------------------------------------------

@pytest.mark.parametrize("board", sorted(CONSOLES))
def test_the_kernels_logo_is_the_boot_screens_picture(lib, board):
    """The kernel lights the glass with the logo before any VM (moy_boot.c),
    and the themed boot screen follows: the two are one picture, or the
    machine appears to start twice. The C raster against console.draw_splash
    on the host canvas, word for word, on every console's logical screen."""
    from runtime import console, host_canvas
    w, h = CONSOLES[board][:2]
    g = Geom()
    lib.moy_rgeom_init(ctypes.byref(g), w, h, 0, 0)
    fb = (u16 * (w * h))()
    lib.moy_boot_logo_render(fb, ctypes.byref(g))
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

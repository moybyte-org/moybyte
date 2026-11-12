"""The native platform layer's guard (docs/refactor_2026-11.md, phase 1).

Our C decides what it is built for, reads the clock, makes a lock, allocates,
starts a task and names an optional symbol through one owner each:

  native/moy_kernel/moy_platform.[ch]  the board probe, the monotonic clock, the mutex
  native/moy_kernel/moy_kmem.[ch]   every allocation, its placement in its flags
  native/moy_kernel/moy_tasks.[ch]  every task, one table and one spawn
  the owning module's header        an optional symbol's declaration; the
                                    consumer writes `#pragma weak X`

A raw use anywhere else is a second copy of the decision, and copies drift.
The allowlist names each exception and why it stays; an entry whose file no
longer has the use fails too, so the list only shrinks.
"""

import fnmatch
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "native")

# Trees we carry but do not write: their own makers' decisions.
VENDORED = (
    "moy_audio/libmoy/", "moy_gfx/libmoy/", "moy_wasm/wamr/", "moy_lua/lua/",
    "moy_ssh/dropbear/", "moy_elf/elf_loader/", "moy_sh/dash/", "moy_net/mbedtls/",
    "moy_net/monocypher/", "moy_media/minimp3", "p4/moy_dsi/vendor",
)

RULES = {
    "probe": re.compile(r'__has_include\s*\(\s*["<]esp_heap_caps\.h'),
    "clock": re.compile(r"\besp_timer_get_time\s*\(|\bclock_gettime\s*\(\s*CLOCK_MONOTONIC"),
    "mutex": re.compile(r"\b(xSemaphoreCreate\w*|pthread_mutex_init|sem_init)\s*\("),
    "alloc": re.compile(r"\b(heap_caps_(?!get_)\w*alloc\w*|malloc|calloc|realloc|aligned_alloc)"
                        r"\s*\("),
    "task": re.compile(r"\bxTaskCreate\w*\s*\("),
    # A declaration made weak, the attribute after it or before it, or a macro
    # that spells the attribute (a weak DEFINITION, a default an owner may
    # replace, is the owner's own).
    "weak": re.compile(r"__attribute__\s*\(\(\s*weak\s*\)\)\s*;"
                       r"|^[ \t]*__attribute__\s*\(\(\s*weak\s*\)\)[^;{}#]*;"
                       r"|#define\s+\w+\s+__attribute__\s*\(\(\s*weak\s*\)\)", re.M),
}

OWNERS = {
    "probe": ("moy_kernel/moy_platform.h",),
    "clock": ("moy_kernel/moy_platform.h",),
    "mutex": ("moy_kernel/moy_platform.h", "moy_kernel/moy_platform.c"),
    "alloc": ("moy_kernel/moy_kmem.h", "moy_kernel/moy_kmem.c"),
    "task": ("moy_kernel/moy_tasks.c",),
    "weak": (),
}

HARNESS = "a host test harness: never in an image"
FLUSH = ("the panel flush: tests/moy_flush_harness compiles it with no board, "
         "stubbing the clock and the semaphores")
P4 = "the P4 silicon tier (native/p4): its drivers own their DMA, locks and tasks"

# (glob, rule): why it stays raw.
ALLOWED = {
    ("*/fuzz_*.c", "alloc"): HARNESS,
    ("*/*_host.c", "alloc"): HARNESS,
    ("*/host/*.c", "alloc"): HARNESS,
    ("moy_flush/*", "clock"): FLUSH,
    ("moy_flush/*", "mutex"): FLUSH,
    ("moy_flush/*", "alloc"): FLUSH,
    ("moy_flush/*", "task"): FLUSH,
    ("p4/*", "probe"): P4,
    ("p4/*", "clock"): P4,
    ("p4/*", "mutex"): P4,
    ("p4/*", "alloc"): P4,
    ("p4/*", "task"): P4,
    ("moy_sh/moy_sh_cmds.c", "clock"):
        "built under dash's shim, whose macros collide with the FreeRTOS headers "
        "moy_platform.h brings",
    ("moy_alloc/modmoy_alloc.c", "alloc"):
        "the DMA buffer allocator: the caller's caps are the allocation",
    ("moy_glass/modmoy_glass.c", "alloc"):
        "the canvas pixels: aligned DMA-capable blocks the PPA and the panel read",
    ("moy_kernel/moy_devbus.c", "alloc"): "a bus transfer's DMA bounce buffer",
    ("moy_net/moy_lora.c", "alloc"): "the radio's SPI transfer buffers, DMA-capable",
    ("moy_sd/moy_sd.c", "alloc"): "the card's bounce buffer, in the caps the host's DMA takes",
    ("moy_elf/modmoy_elf.c", "alloc"): "a program's memory: aligned to its sandbox's unit",
    ("moy_elf/moy_os.c", "alloc"): "a program's memory and stacks: aligned to its sandbox's unit",
    ("moycore/moycore_lua.c", "alloc"):
        "the Lua state's allocator over its own pool (lua_Alloc's realloc contract)",
    ("moy_elf/moy_os.c", "mutex"):
        "a compiled program's pthread and semaphore calls, which it creates and owns",
    ("moy_audio/moy_aud_out.c", "mutex"):
        "made at start and deleted at stop: NULL is the stopped output",
    ("moy_net/moy_lora.c", "mutex"): "the radio's lock is taken with a timeout",
    ("moy_net/moy_seal.c", "mutex"): "made in PSRAM: its control block spends no internal SRAM",
    ("moy_sh/moy_sh_board.c", "mutex"): "a runner's hand-off: binary semaphores and a condition",
    ("moy_usbdisk/moy_usbdisk.c", "mutex"): "a binary semaphore: the task's done signal",
    ("moy_wasm/modmoy_wasm.c", "mutex"): "binary semaphores: a session's hand-offs",
    ("moy_kernel/moy_kernel.c", "task"):
        "mp_task: the esp32 port's own task, pinned as the port starts it",
    ("moy_app/moy_app_wasm.c", "weak"):
        "the toolkit's wasm rows, which tools/gen_os_table.py declares weak (UI_WEAK) "
        "in moy_app_wasm_rows.h",
    ("moy_gfx/modmoy_gfx.c", "weak"): "esp_native_code_free_all is MicroPython's, no header of ours",
    ("moy_kernel/modmoy_guard.c", "weak"):
        "moybyte_gc_pause is the patched collector's (tools/patch_gc_meters.py)",
    ("moy_kernel/moy_loop_board.c", "weak"):
        "moybyte_gc_pause is the patched collector's (tools/patch_gc_meters.py)",
}


def _ours():
    for dp, _dn, fn in os.walk(ROOT):
        for f in sorted(fn):
            if f.endswith((".c", ".h")):
                rel = os.path.relpath(os.path.join(dp, f), ROOT).replace(os.sep, "/")
                if not any(v in rel for v in VENDORED):
                    yield rel


def _code(rel):
    with open(os.path.join(ROOT, rel), errors="replace") as fh:
        text = fh.read()
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _allowed(rel, rule):
    return next((k for k in ALLOWED if k[1] == rule and fnmatch.fnmatch(rel, k[0])), None)


def test_the_platform_has_one_owner_per_decision():
    bad, used = [], set()
    for rel in _ours():
        code = _code(rel)
        for rule, rx in RULES.items():
            n = len(rx.findall(code))
            if not n or rel in OWNERS[rule]:
                continue
            key = _allowed(rel, rule)
            if key is None:
                bad.append("%s: %d raw %s use(s)" % (rel, n, rule))
            else:
                used.add(key)
    assert not bad, "outside the platform layer (tests/test_native_platform.py):\n" + "\n".join(bad)
    stale = sorted(set(ALLOWED) - used)
    assert not stale, "allowlist entries with nothing left to allow: %s" % stale


def _header_decls():
    """Every prototype and extern our headers declare, by name: one line each."""
    decls = {}
    for rel in _ours():
        if not rel.endswith(".h"):
            continue
        code = re.sub(r'extern "C" \{|#if\w* (?:defined\()?__cplusplus\)?\s*\}\s*#endif', "",
                      _code(rel))
        code = re.sub(r"^[ \t]*#[^\n]*", "", code, flags=re.M)
        depth, cur = 0, []
        for ch in code:
            if ch == "{":
                depth, cur = depth + 1, []
            elif ch == "}":
                depth, cur = depth - 1, []
            elif depth == 0 and ch == ";":
                st = " ".join("".join(cur).split())
                cur = []
                m = (re.match(r"^(?:extern\s+)?[\w\s\*]*?\b(\w+)\s*\(", st)
                     or re.match(r"^extern\s[^(]*?\b(\w+)\s*(\[[^\]]*\])?$", st))
                if m and not st.startswith(("typedef", "static")):
                    decls.setdefault(m.group(1), {})[os.path.basename(rel)] = st + ";"
            elif depth == 0:
                cur.append(ch)
    return decls


FOREIGN_WEAK = {
    # FatFs (MicroPython's lib/oofatfs), where an image builds the FAT VFS.
    "f_mount", "f_open", "f_read", "f_write", "f_close", "f_lseek", "f_stat", "f_opendir",
    "f_readdir", "f_closedir", "f_mkdir", "f_unlink", "f_rename", "f_chdir",
}

UNGATED = re.compile(r'(?P<pre>[^\n]*\n)#include "(?P<h>[\w.]+)"\n#pragma weak')


def _module(rel):
    parts = rel.split("/")
    return "/".join(parts[:2]) if parts[0] == "p4" else parts[0]


HEADERS = {os.path.basename(rel): _module(rel) for rel in _ours() if rel.endswith(".h")}

# The header, and where it needs one the build may lack, that one too.
GATED = re.compile(r'#if __has_include\("([\w.]+)"\)(?: && __has_include\("[\w./]+"\))?\n'
                   r'#include "\1"\n#else\n(.*?)\n#endif\n', re.S)


def test_an_optional_symbol_is_declared_by_its_owner():
    """`#pragma weak X` names a symbol its owner's header declares. Where the
    owner is another module than the kernel (one an image denies, or a host,
    unix, browser or cart build leaves out), the consumer includes the header
    when it is there and otherwise keeps the owner's declaration, word for
    word: this test is what keeps that copy generated from the header."""
    decls = _header_decls()
    bad = []
    for rel in _ours():
        code = _code(rel)
        for sym in re.findall(r"#pragma weak (\w+)", code):
            if sym not in decls and sym not in FOREIGN_WEAK:
                bad.append("%s: #pragma weak %s, which no header of ours declares" % (rel, sym))
        for m in UNGATED.finditer(code):
            header = m.group("h")
            owner = HEADERS.get(header)
            if (not m.group("pre").startswith("#if __has_include") and owner is not None
                    and owner not in ("moy_kernel", _module(rel))):
                bad.append("%s: #pragma weak after %s, a module another build may not have, "
                           "ungated" % (rel, header))
        for m in GATED.finditer(code):
            header = m.group(1)
            for line in m.group(2).split("\n"):
                st = " ".join(line.split())
                name = re.match(r"^(?:extern\s+)?[\w\s\*]*?\b(\w+)\s*[(\[]", st)
                own = decls.get(name.group(1), {}).get(header) if name else None
                if own != st:
                    bad.append("%s: %r is not %s's declaration (%r)" % (rel, st, header, own))
    assert not bad, "\n".join(bad)

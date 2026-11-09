// moy_elf: the native tier's loader and its MicroPython module (README.md).
//
// A module file is read from the store into PSRAM, its signature checked
// (moy_ota_admit_module: a present one must verify, an absent one needs the
// UNSIGNED flag, the cart's consent), its verdict taken
// (moy_elf_check), and Espressif's elf_loader (elf_loader/, vendored)
// relocates it into PSRAM, resolving no name: a module imports nothing. Its
// constructors run once (esp_elf_request), and the process is moy_os's. On a
// P4 the module runs in U-mode (moy_umode.h): the loader's one segment is the
// block, 128-aligned after the gate this file writes, and its constructors run
// on its first thread.
//
//   moy_elf.CHIP, ROWS, VERSION
//   moy_elf.command(path, argv, roots, pid, id, bg_pid, budget_ms[, flags]) -> (status, err)
//   moy_elf.start(path, argv, roots, pid, id, budget_ms[, flags]) -> (0 or -3, err): the
//       same run left going; step(pid, max_ms) -> -100 (running) or its exit
//       status; cancel(pid) -> 130. flags: NET (1), the socket rows; UNSIGNED
//       (2), the cart's consent admits a module with no signature
//   moy_elf.on_net(fn): fn(pid, hold) when a process opens its first socket
//       (1) and at its end (0), on this task; None: nothing holds the network
//   moy_elf.service(path, pid, id[, flags[, run]]) -> None, or the reason it did not
//       start; run the bytes of its app run (moy_app's `run`), its app calls' grant
//   moy_elf.reap() -> [(pid, status)] of the services that ended, by themselves or
//       killed by the watchdog (moy_os_reap); the scheduler's poll calls it, and
//       the loop's job runs the watchdog once a frame while a service lives
//   moy_elf.stop(pid) -> status, or -1 for no such service
//   moy_elf.info(pid) -> (threads, handles, wakes, events, mapped, cpu_us) or None
//   moy_elf.stats() -> (started, swept, killed, leaked)
//   moy_elf.mem() -> (internal free, internal largest, psram free, psram largest)
//   moy_elf.load() -> (read_us, check_us, reloc_us, internal free before, after):
//       the last load's cost, which the calling task waits out
//   moy_elf.faults() -> [(pid, name, cause, pc, addr)], the newest first: the
//       U-mode modules killed where they stood (moy_os_faults); [] on an S3
//   moy_elf.umode() -> (pmpcfg0..3) on this core, or None where modules do not
//       run in U-mode (an S3; a P4 whose bootloader locked its own PMP layout)
//
// `command`, `start`, `step` and `service` run on the MicroPython task: a
// command's whole run holds it (its session's calls are served there), a
// step holds it for its max_ms, a service's start not past the start.

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"
#include "py/stream.h"
#include "py/binary.h"
#include "py/builtin.h"

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#include "esp_elf.h"
#include "moy_elf.h"
#include "moy_elf_maps.h"
#include "moy_net.h"
#include "moy_ota.h"
#include "moy_os.h"
#include "moy_log.h"
#include "moy_loop.h"
#include "moy_sh.h"
#ifdef MOY_OS_UMODE
#include "esp_cache.h"
#include "riscv/csr.h"
#include "moy_umode.h"
#endif

#define PSRAM_CAPS (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)
#define INTERNAL_CAPS (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)
#define FILE_MAX (4u * 1024u * 1024u)
#define ERR_MAX 160

#if CONFIG_IDF_TARGET_ESP32S3
#define CHIP "esp32s3"
#define CHIP_QSTR MP_QSTR_esp32s3
#define MACHINE MOY_ELF_EM_XTENSA
#elif CONFIG_IDF_TARGET_ESP32P4
#define CHIP "esp32p4"
#define CHIP_QSTR MP_QSTR_esp32p4
#define MACHINE MOY_ELF_EM_RISCV
#else
#error "moy_elf: a chip the SDK does not build for"
#endif

// The last load's cost: the file's read, its signature, verdict and sha256
// (moy_elf_maps.h), its relocation (the loader's cache flush included), all on the calling task;
// and the internal SRAM free before it and once the module is loaded.
static struct {
    int64_t read_us, check_us, reloc_us;
    size_t int_before, int_loaded;
} g_load;

// A module imports nothing, so every name is unknown.
static uintptr_t resolve_none(const char *name) {
    (void)name;
    return 0;
}

#ifdef MOY_OS_UMODE
// The loader's executable allocation (a P4 has one: the segment) is a module's
// block: the gate first, then the segment, 128-aligned and a multiple of 128
// long (-Wl,--wrap=esp_elf_malloc), zeroed, so nothing of the heap's last
// holder shows in it. The free finds the block's start and scrubs it.
#define GATE MOY_U_GATE(MOY_OS_ROWS)
#define BLOCKS MOY_OS_PROCS
static uint8_t *g_block[BLOCKS];
static uint8_t *g_last;

void *__real_esp_elf_malloc(uint32_t n, bool exec);
void __real_esp_elf_free(void *ptr);

void *__wrap_esp_elf_malloc(uint32_t n, bool exec) {
    if (!exec) {
        return __real_esp_elf_malloc(n, exec);
    }
    for (int i = 0; i < BLOCKS; i++) {
        if (g_block[i] == NULL) {
            uint8_t *b = heap_caps_aligned_alloc(MOY_U_ALIGN, GATE + MOY_U_UP(n), PSRAM_CAPS);
            if (b == NULL) {
                return NULL;
            }
            memset(b, 0, GATE + MOY_U_UP(n));
            g_block[i] = b;
            g_last = b;
            return b + GATE;
        }
    }
    return NULL;
}

void __wrap_esp_elf_free(void *ptr) {
    for (int i = 0; ptr != NULL && i < BLOCKS; i++) {
        if (g_block[i] != NULL && g_block[i] + GATE == ptr) {
            memset(g_block[i], 0, heap_caps_get_allocated_size(g_block[i]));
            heap_caps_free(g_block[i]);
            g_block[i] = NULL;
            return;
        }
    }
    __real_esp_elf_free(ptr);
}

// Where the module's text ends and its data starts (vaddrs): from its program
// headers, which the loader read the segment by.
static int u_layout(const uint8_t *buf, size_t len, uint32_t *text_end, uint32_t *data_at) {
    const elf32_hdr_t *eh = (const elf32_hdr_t *)buf;
    *text_end = 0;
    *data_at = UINT32_MAX;
    if (len < sizeof *eh || eh->phoff > len
        || eh->phnum > (len - eh->phoff) / sizeof(elf32_phdr_t)) {
        return -1;
    }
    const elf32_phdr_t *ph = (const elf32_phdr_t *)(buf + eh->phoff);
    for (int i = 0; i < eh->phnum; i++) {
        if (ph[i].type != PT_LOAD) {
            continue;
        }
        if (ph[i].flags & 2u) {             // PF_W
            *data_at = ph[i].vaddr < *data_at ? ph[i].vaddr : *data_at;
        } else if (ph[i].vaddr + ph[i].memsz > *text_end) {
            *text_end = ph[i].vaddr + ph[i].memsz;
        }
    }
    return 0;
}

// The module's block, gate and constructors into *m: 0, or -1 with the reason.
static int u_module(esp_elf_t *elf, uint32_t text_end, uint32_t data_at, moy_os_module_t *m,
                    char *err, size_t errlen) {
    uint32_t base = (uint32_t)g_last;
    uint32_t vbase = (uint32_t)elf->psegment - elf->svaddr;
    uint32_t hi = (uint32_t)elf->psegment + MOY_U_UP(elf->segment_size);
    uint32_t rx = data_at == UINT32_MAX ? hi : MOY_U_DOWN(vbase + data_at);
    if (g_last == NULL || base + GATE != (uint32_t)elf->psegment || rx < vbase + text_end
        || rx > hi) {
        snprintf(err, errlen, "refused: its text and its data share a 128-byte line");
        return -1;
    }
    m->ulo = base;
    m->urx = rx;
    m->uhi = hi;
    m->uvbase = vbase;
    m->utable = base;
    moy_u_gate(base, MOY_OS_VERSION, MOY_OS_ROWS, &m->uret);
    uint32_t entry = (uint32_t)elf->entry;
    if (entry < base + GATE || entry >= rx) {
        snprintf(err, errlen, "refused: its entry is not in its text");
        return -1;
    }
    void (**lists[2])(void) = { elf->preinit_array, elf->init_array };
    uint32_t counts[2] = { elf->preinit_count, elf->init_count };
    for (int l = 0; l < 2; l++) {
        for (uint32_t i = 0; i < counts[l]; i++) {
            bool rev = l == 1 && elf->init_reverse;
            uint32_t fn = (uint32_t)lists[l][rev ? counts[l] - 1 - i : i];
            if (fn == 0 || fn == UINT32_MAX) {
                continue;
            }
            if (fn < base + GATE || fn >= rx || m->uinits == MOY_OS_INITS) {
                snprintf(err, errlen, "refused: a constructor outside its text, or too many");
                return -1;
            }
            m->uinit[m->uinits++] = fn;
        }
    }
    return 0;
}
#endif

_Static_assert(MOY_ELF_MAPS_SLOTS >= MOY_OS_PROCS, "a slot for every process's module");

// The module's code where moy_elf_maps.h says: the segment on a P4, every
// section at one offset; on an S3 its text, at the instruction bus's alias.
static void map_module(esp_elf_t *elf, uint32_t pid, const uint8_t sha[32]) {
#if CONFIG_ELF_LOADER_BUS_ADDRESS_MIRROR
    const esp_elf_sec_t *t = &elf->sec[ELF_SEC_TEXT];
    moy_elf_maps_add(elf, pid, t->v_addr, (uint32_t)esp_elf_runtime_base(elf), t->size, sha);
#else
    moy_elf_maps_add(elf, pid, elf->svaddr, (uint32_t)elf->psegment, elf->segment_size, sha);
#endif
    const moy_elf_map_t *m = NULL;
    const moy_elf_maps_t *all = moy_elf_maps();
    for (unsigned i = 0; i < MOY_ELF_MAPS_SLOTS; i++) {
        if (all->map[i].seq == all->seq) {
            m = &all->map[i];
        }
    }
    if (m != NULL) {
        moy_log_say(MOY_LOG_INFO, pid, "module %02x%02x%02x%02x%02x%02x%02x%02x: %08lx+%lx at %08lx",
                    sha[0], sha[1], sha[2], sha[3], sha[4], sha[5], sha[6], sha[7],
                    (unsigned long)m->vaddr, (unsigned long)m->size, (unsigned long)m->addr);
    }
}

static void unload(void *loaded) {
    esp_elf_t *elf = loaded;
    moy_elf_maps_drop(elf);
    esp_elf_deinit(elf);
    heap_caps_free(elf);
}

// The file at `path` read, admitted (`flags`' MOY_OS_UNSIGNED: the cart's
// consent), checked and relocated into *m for process `pid`, and its place
// recorded for a core dump (moy_elf_maps.h): 0, or -1 with the reason in err.
// Raises only when the file cannot be read.
static int load(mp_obj_t path, uint32_t flags, uint32_t pid, moy_os_module_t *m, char *err,
                size_t errlen) {
    memset(m, 0, sizeof *m);
#ifdef MOY_OS_UMODE
    if (moy_u_ready() != 0) {
        snprintf(err, errlen, "refused: this console cannot isolate a module (an interrupt "
                 "table its isolation was not built for, or a bootloader that locked the PMP)");
        return -1;
    }
#endif
    g_load.int_before = heap_caps_get_free_size(INTERNAL_CAPS);
    int64_t t0 = esp_timer_get_time();
    mp_obj_t args[2] = { path, MP_OBJ_NEW_QSTR(MP_QSTR_rb) };
    mp_obj_t f = mp_call_function_n_kw(MP_OBJ_FROM_PTR(&mp_builtin_open_obj), 2, 0, args);
    int e = 0;
    mp_off_t size = mp_stream_seek(f, 0, MP_SEEK_END, &e);
    if (e == 0) {
        mp_stream_seek(f, 0, MP_SEEK_SET, &e);
    }
    if (e != 0 || size <= 0 || size > FILE_MAX) {
        mp_stream_close(f);
        snprintf(err, errlen, "the module is empty, unreadable or too big");
        return -1;
    }
    uint8_t *buf = heap_caps_malloc((size_t)size, PSRAM_CAPS);
    if (buf == NULL) {
        mp_stream_close(f);
        snprintf(err, errlen, "out of memory: no PSRAM for the module file");
        return -1;
    }
    mp_uint_t got = mp_stream_rw(f, buf, (mp_uint_t)size, &e, MP_STREAM_RW_READ);
    mp_stream_close(f);
    if (e != 0 || got != (mp_uint_t)size) {
        heap_caps_free(buf);
        snprintf(err, errlen, "the module would not read");
        return -1;
    }
    size_t len = (size_t)size;
    int64_t t1 = esp_timer_get_time();
    const char *bad = moy_ota_admit_module(buf, len, MOY_ELF_SIG_SCHEME, CHIP,
                                           (flags & MOY_OS_UNSIGNED) != 0, NULL, 0, &len);
    if (bad != NULL) {
        heap_caps_free(buf);
        snprintf(err, errlen, "refused: %s", bad);
        return -1;
    }
    moy_elf_info_t info;
    char why[ERR_MAX - sizeof "refused: "];
    if (moy_elf_check(buf, len, MACHINE, MOY_OS_ROWS, 0, &info, why, sizeof why) != 0) {
        heap_caps_free(buf);
        snprintf(err, errlen, "refused: %s", why);
        return -1;
    }
    uint8_t sha[32];
    moy_net_sha256(buf, len, sha);
    int64_t t2 = esp_timer_get_time();
    esp_elf_t *elf = heap_caps_calloc(1, sizeof *elf, PSRAM_CAPS);
    if (elf == NULL) {
        heap_caps_free(buf);
        snprintf(err, errlen, "out of memory");
        return -1;
    }
    esp_elf_init(elf);
    elf_set_symbol_resolver(resolve_none);
#ifdef MOY_OS_UMODE
    uint32_t text_end, data_at;
    if (u_layout(buf, len, &text_end, &data_at) != 0) {
        heap_caps_free(buf);
        heap_caps_free(elf);
        snprintf(err, errlen, "refused: its program headers");
        return -1;
    }
    g_last = NULL;
#endif
    int r = esp_elf_relocate(elf, buf, len);
    heap_caps_free(buf);
    if (r != 0) {
        esp_elf_deinit(elf);
        heap_caps_free(elf);
        snprintf(err, errlen, "load: the module would not relocate (%d)", r);
        return -1;
    }
#ifdef MOY_OS_UMODE
    if (u_module(elf, text_end, data_at, m, err, errlen) != 0) {
        esp_elf_deinit(elf);
        heap_caps_free(elf);
        return -1;
    }
#else
    esp_elf_request(elf, 0, 0, NULL);
#endif
    map_module(elf, pid, sha);
    g_load.read_us = t1 - t0;
    g_load.check_us = t2 - t1;
    g_load.reloc_us = esp_timer_get_time() - t2;
    g_load.int_loaded = heap_caps_get_free_size(INTERNAL_CAPS);
    m->entry = (moy_elf_entry_fn)(uintptr_t)elf->entry;
    m->loaded = elf;
    m->unload = unload;
    return 0;
}

static mp_obj_t result(int status, const char *err) {
    mp_obj_t out[2] = {
        mp_obj_new_int(status),
        status < 0 ? mp_obj_new_str(err, strlen(err)) : mp_const_none,
    };
    return mp_obj_new_tuple(2, out);
}

// A run's bytes (moy_app.run's answer) copied into `out`: `out`, or NULL for
// None, which serves the process no app calls.
static const moy_app_run_t *run_of(mp_obj_t o, moy_app_run_t *out) {
    if (o == mp_const_none) {
        return NULL;
    }
    mp_buffer_info_t b;
    mp_get_buffer_raise(o, &b, MP_BUFFER_READ);
    if (b.len != sizeof *out) {
        mp_raise_ValueError(MP_ERROR_TEXT("not a run"));
    }
    memcpy(out, b.buf, sizeof *out);
    return out;
}

static mp_obj_t mod_command(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    size_t argc;
    mp_obj_t *items;
    mp_obj_get_array(args[1], &argc, &items);
    size_t roots_n = 0;
    const char *roots = args[2] == mp_const_none ? NULL : mp_obj_str_get_data(args[2], &roots_n);
    uint32_t pid = (uint32_t)mp_obj_get_int(args[3]);
    const char *id = mp_obj_str_get_str(args[4]);
    uint32_t bg_pid = (uint32_t)mp_obj_get_int(args[5]);
    uint32_t budget = (uint32_t)mp_obj_get_int(args[6]);
    char err[ERR_MAX];
    err[0] = 0;
    moy_sh_t *s = moy_sh_running();
    if (s == NULL) {
        return result(MOY_OS_REFUSED, "needs a shell session");
    }
    uint32_t flags = n_args > 7 ? (uint32_t)mp_obj_get_int(args[7]) : 0u;
    moy_os_module_t m;
    if (load(args[0], flags, pid, &m, err, sizeof err) != 0) {
        return result(MOY_OS_REFUSED, err);
    }
    const char **argv = m_new(const char *, argc ? argc : 1);
    for (size_t i = 0; i < argc; i++) {
        argv[i] = mp_obj_str_get_str(items[i]);
    }
    moy_app_run_t run;
    const moy_app_run_t *rp = run_of(n_args > 8 ? args[8] : mp_const_none, &run);
    int status = moy_os_command(s, &m, NULL, (int)argc, argv, roots, pid, id, bg_pid, budget,
                                flags, rp, err, sizeof err);
    m_del(const char *, argv, argc ? argc : 1);
    return result(status, err);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_command_obj, 7, 9, mod_command);

static mp_obj_t mod_start(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    size_t argc;
    mp_obj_t *items;
    mp_obj_get_array(args[1], &argc, &items);
    size_t roots_n = 0;
    const char *roots = args[2] == mp_const_none ? NULL : mp_obj_str_get_data(args[2], &roots_n);
    uint32_t pid = (uint32_t)mp_obj_get_int(args[3]);
    const char *id = mp_obj_str_get_str(args[4]);
    uint32_t budget = (uint32_t)mp_obj_get_int(args[5]);
    char err[ERR_MAX];
    err[0] = 0;
    moy_sh_t *s = moy_sh_running();
    if (s == NULL) {
        return result(MOY_OS_REFUSED, "needs a shell session");
    }
    uint32_t flags = n_args > 6 ? (uint32_t)mp_obj_get_int(args[6]) : 0u;
    moy_os_module_t m;
    if (load(args[0], flags, pid, &m, err, sizeof err) != 0) {
        return result(MOY_OS_REFUSED, err);
    }
    const char **argv = m_new(const char *, argc ? argc : 1);
    for (size_t i = 0; i < argc; i++) {
        argv[i] = mp_obj_str_get_str(items[i]);
    }
    moy_app_run_t run;
    const moy_app_run_t *rp = run_of(n_args > 7 ? args[7] : mp_const_none, &run);
    int status = moy_os_command_start(s, &m, NULL, (int)argc, argv, roots, pid, id, 0, budget,
                                      flags, rp, err, sizeof err);
    m_del(const char *, argv, argc ? argc : 1);
    return result(status, err);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_start_obj, 6, 8, mod_start);

// The network's holder (moy_os_set_net): fn(pid, hold) on the VM's task, the
// one that serves a command's session.
MP_REGISTER_ROOT_POINTER(mp_obj_t moy_elf_net);

static void net_hook(void *ctx, uint32_t pid, int hold) {
    (void)ctx;
    mp_obj_t fn = MP_STATE_VM(moy_elf_net);
    if (fn == MP_OBJ_NULL || fn == mp_const_none) {
        return;
    }
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_call_function_2(fn, mp_obj_new_int(pid), mp_obj_new_int(hold));
        nlr_pop();
    }
}

static mp_obj_t mod_on_net(mp_obj_t fn) {
    MP_STATE_VM(moy_elf_net) = fn;
    moy_os_set_net(fn == mp_const_none ? NULL : net_hook, NULL);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_on_net_obj, mod_on_net);

static mp_obj_t mod_step(mp_obj_t pid, mp_obj_t max_ms) {
    return mp_obj_new_int(moy_os_command_step((uint32_t)mp_obj_get_int(pid),
                                              (uint32_t)mp_obj_get_int(max_ms) * 1000u));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_step_obj, mod_step);

static mp_obj_t mod_cancel(mp_obj_t pid) {
    return mp_obj_new_int(moy_os_command_stop((uint32_t)mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_cancel_obj, mod_cancel);

// The loop's job (moy_loop.h) while a service lives: the watchdog once a
// frame on the console's task (moy_os_watch), where no VM wake is due.
bool moy_loop_job(moy_loop_job_fn fn, void *arg) __attribute__((weak));

static bool watch_job(void *arg) {
    (void)arg;
    return moy_os_watch();
}

static mp_obj_t mod_service(size_t n_args, const mp_obj_t *args) {
    if (moy_loop_job != NULL && !moy_loop_job(watch_job, NULL)) {
        return mp_obj_new_str("no room for its watchdog", 24);
    }
    char err[ERR_MAX];
    uint32_t flags = n_args > 3 ? (uint32_t)mp_obj_get_int(args[3]) : 0u;
    moy_app_run_t run;
    const moy_app_run_t *rp = run_of(n_args > 4 ? args[4] : mp_const_none, &run);
    uint32_t pid = (uint32_t)mp_obj_get_int(args[1]);
    moy_os_module_t m;
    if (load(args[0], flags, pid, &m, err, sizeof err) != 0) {
        return mp_obj_new_str(err, strlen(err));
    }
    if (moy_os_service(&m, NULL, pid, mp_obj_str_get_str(args[2]),
                       rp, err, sizeof err) == NULL) {
        return mp_obj_new_str(err, strlen(err));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_service_obj, 3, 5, mod_service);

// game(path, pid, id, flags, w, h, run=None) -> None, or why it did not start.
static mp_obj_t mod_game(size_t n_args, const mp_obj_t *args) {
    char err[ERR_MAX];
    moy_app_run_t run;
    const moy_app_run_t *rp = run_of(n_args > 6 ? args[6] : mp_const_none, &run);
    moy_os_module_t m;
    if (load(args[0], (uint32_t)mp_obj_get_int(args[3]), (uint32_t)mp_obj_get_int(args[1]), &m, err, sizeof err) != 0) {
        return mp_obj_new_str(err, strlen(err));
    }
    if (moy_os_game(&m, NULL, (uint32_t)mp_obj_get_int(args[1]), mp_obj_str_get_str(args[2]),
                    mp_obj_get_int(args[4]), mp_obj_get_int(args[5]), rp, err, sizeof err)
            == NULL) {
        return mp_obj_new_str(err, strlen(err));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_game_obj, 6, 7, mod_game);

// frame(pid, held, pressed, ms, max_us) -> moy_os_game_frame's answer.
static mp_obj_t mod_frame(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    return MP_OBJ_NEW_SMALL_INT(moy_os_game_frame(
        (uint32_t)mp_obj_get_int(args[0]), (uint32_t)mp_obj_get_int(args[1]),
        (uint32_t)mp_obj_get_int(args[2]), (uint32_t)mp_obj_get_int(args[3]),
        (uint32_t)mp_obj_get_int(args[4])));
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_frame_obj, 5, 5, mod_frame);

// screen(pid) -> a view of the game's frame for its life, or None.
static mp_obj_t mod_screen(mp_obj_t pid) {
    int w = 0, h = 0;
    const uint8_t *fb = moy_os_game_screen((uint32_t)mp_obj_get_int(pid), &w, &h);
    if (fb == NULL) {
        return mp_const_none;
    }
    return mp_obj_new_memoryview(BYTEARRAY_TYPECODE, (size_t)w * (size_t)h, (void *)fb);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_screen_obj, mod_screen);

static mp_obj_t mod_game_stop(mp_obj_t pid) {
    return mp_obj_new_int(moy_os_game_stop((uint32_t)mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_game_stop_obj, mod_game_stop);

static mp_obj_t mod_reap(void) {
    uint32_t pids[MOY_OS_PROCS];
    int32_t codes[MOY_OS_PROCS];
    uint32_t n = moy_os_reap(pids, codes, MOY_OS_PROCS);
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (uint32_t i = 0; i < n; i++) {
        mp_obj_t t[2] = { mp_obj_new_int_from_uint(pids[i]), mp_obj_new_int(codes[i]) };
        mp_obj_list_append(out, mp_obj_new_tuple(2, t));
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_reap_obj, mod_reap);

static mp_obj_t mod_stop(mp_obj_t pid) {
    return mp_obj_new_int(moy_os_stop((uint32_t)mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_stop_obj, mod_stop);

static mp_obj_t mod_info(mp_obj_t pid) {
    moy_os_info_t i;
    if (moy_os_info((uint32_t)mp_obj_get_int(pid), &i) != 0) {
        return mp_const_none;
    }
    mp_obj_t t[6] = {
        mp_obj_new_int_from_uint(i.threads), mp_obj_new_int_from_uint(i.handles),
        mp_obj_new_int_from_uint(i.wakes), mp_obj_new_int_from_uint(i.events),
        mp_obj_new_int_from_uint(i.mapped), mp_obj_new_int_from_uint(i.cpu_us),
    };
    return mp_obj_new_tuple(6, t);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_info_obj, mod_info);

static mp_obj_t mod_stats(void) {
    moy_os_stats_t s;
    moy_os_stats(&s);
    mp_obj_t t[4] = {
        mp_obj_new_int_from_uint(s.started), mp_obj_new_int_from_uint(s.swept),
        mp_obj_new_int_from_uint(s.killed), mp_obj_new_int_from_uint(s.leaked),
    };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_stats_obj, mod_stats);

static mp_obj_t mod_mem(void) {
    mp_obj_t t[4] = {
        mp_obj_new_int_from_uint(heap_caps_get_free_size(INTERNAL_CAPS)),
        mp_obj_new_int_from_uint(heap_caps_get_largest_free_block(INTERNAL_CAPS)),
        mp_obj_new_int_from_uint(heap_caps_get_free_size(PSRAM_CAPS)),
        mp_obj_new_int_from_uint(heap_caps_get_largest_free_block(PSRAM_CAPS)),
    };
    return mp_obj_new_tuple(4, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_mem_obj, mod_mem);

// check() -> (heap intact, the least free stack in bytes of this task and of
// any native thread's kernel stack): what a test asks after each attack.
static mp_obj_t mod_check(void) {
    uint32_t low = moy_os_stack_low();
    mp_obj_t t[3] = {
        mp_obj_new_bool(heap_caps_check_integrity_all(true)),
        mp_obj_new_int_from_uint((uint32_t)uxTaskGetStackHighWaterMark(NULL)),
        low == UINT32_MAX ? mp_const_none : mp_obj_new_int_from_uint(low),
    };
    return mp_obj_new_tuple(3, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_check_obj, mod_check);

// load() -> (read_us, check_us, reloc_us, internal free before, internal
// free loaded): the last load's.
static mp_obj_t mod_load(void) {
    mp_obj_t t[5] = {
        mp_obj_new_int_from_ll(g_load.read_us), mp_obj_new_int_from_ll(g_load.check_us),
        mp_obj_new_int_from_ll(g_load.reloc_us), mp_obj_new_int_from_uint(g_load.int_before),
        mp_obj_new_int_from_uint(g_load.int_loaded),
    };
    return mp_obj_new_tuple(5, t);
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_load_obj, mod_load);

static mp_obj_t mod_faults(void) {
    moy_os_fault_t f[MOY_OS_FAULTS];
    uint32_t n = moy_os_faults(f, MOY_OS_FAULTS);
    mp_obj_t list = mp_obj_new_list(0, NULL);
    for (uint32_t i = 0; i < n; i++) {
        mp_obj_t t[5] = {
            mp_obj_new_int_from_uint(f[i].pid), mp_obj_new_str(f[i].name, strlen(f[i].name)),
            mp_obj_new_int_from_uint(f[i].cause), mp_obj_new_int_from_uint(f[i].pc),
            mp_obj_new_int_from_uint(f[i].addr),
        };
        mp_obj_list_append(list, mp_obj_new_tuple(5, t));
    }
    return list;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_faults_obj, mod_faults);

static mp_obj_t mod_umode(void) {
#ifdef MOY_OS_UMODE
    if (moy_u_ready() != 0) {
        return mp_const_none;
    }
    mp_obj_t t[4] = {
        mp_obj_new_int_from_uint(RV_READ_CSR(pmpcfg0)), mp_obj_new_int_from_uint(RV_READ_CSR(pmpcfg1)),
        mp_obj_new_int_from_uint(RV_READ_CSR(pmpcfg2)), mp_obj_new_int_from_uint(RV_READ_CSR(pmpcfg3)),
    };
    return mp_obj_new_tuple(4, t);
#else
    return mp_const_none;
#endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_umode_obj, mod_umode);

static const mp_rom_map_elem_t moy_elf_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_faults), MP_ROM_PTR(&mod_faults_obj) },
    { MP_ROM_QSTR(MP_QSTR_umode), MP_ROM_PTR(&mod_umode_obj) },
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_elf) },
    { MP_ROM_QSTR(MP_QSTR_CHIP), MP_ROM_QSTR(CHIP_QSTR) },
    { MP_ROM_QSTR(MP_QSTR_ROWS), MP_ROM_INT(MOY_OS_ROWS) },
    { MP_ROM_QSTR(MP_QSTR_VERSION), MP_ROM_INT(MOY_OS_VERSION) },
    { MP_ROM_QSTR(MP_QSTR_command), MP_ROM_PTR(&mod_command_obj) },
    { MP_ROM_QSTR(MP_QSTR_load), MP_ROM_PTR(&mod_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&mod_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_on_net), MP_ROM_PTR(&mod_on_net_obj) },
    { MP_ROM_QSTR(MP_QSTR_NET), MP_ROM_INT(MOY_OS_NET) },
    { MP_ROM_QSTR(MP_QSTR_UNSIGNED), MP_ROM_INT(MOY_OS_UNSIGNED) },
    { MP_ROM_QSTR(MP_QSTR_step), MP_ROM_PTR(&mod_step_obj) },
    { MP_ROM_QSTR(MP_QSTR_cancel), MP_ROM_PTR(&mod_cancel_obj) },
    { MP_ROM_QSTR(MP_QSTR_service), MP_ROM_PTR(&mod_service_obj) },
    { MP_ROM_QSTR(MP_QSTR_reap), MP_ROM_PTR(&mod_reap_obj) },
    { MP_ROM_QSTR(MP_QSTR_game), MP_ROM_PTR(&mod_game_obj) },
    { MP_ROM_QSTR(MP_QSTR_frame), MP_ROM_PTR(&mod_frame_obj) },
    { MP_ROM_QSTR(MP_QSTR_screen), MP_ROM_PTR(&mod_screen_obj) },
    { MP_ROM_QSTR(MP_QSTR_game_stop), MP_ROM_PTR(&mod_game_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_ENDED), MP_ROM_INT(MOY_OS_ENDED) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&mod_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_info), MP_ROM_PTR(&mod_info_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&mod_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_mem), MP_ROM_PTR(&mod_mem_obj) },
    { MP_ROM_QSTR(MP_QSTR_check), MP_ROM_PTR(&mod_check_obj) },
};
static MP_DEFINE_CONST_DICT(moy_elf_globals, moy_elf_globals_table);

const mp_obj_module_t moy_elf_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_elf_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_elf, moy_elf_user_cmodule);

// moy_elf: the native tier's loader and its MicroPython module (README.md).
//
// A module file is read from the store into PSRAM, its signature checked
// (moy_ota_verify_module: a present one must verify), its verdict taken
// (moy_elf_check), and Espressif's elf_loader (elf_loader/, vendored)
// relocates it into PSRAM, resolving no name: a module imports nothing. Its
// constructors run once (esp_elf_request), and the process is moy_os's.
//
//   moy_elf.CHIP, ROWS, VERSION
//   moy_elf.command(path, argv, roots, pid, id, bg_pid, budget_ms) -> (status, err)
//   moy_elf.start(path, argv, roots, pid, id, budget_ms) -> (0 or -3, err): the
//       same run left going; step(pid, max_ms) -> -100 (running) or its exit
//       status; cancel(pid) -> 130
//   moy_elf.service(path, pid, id) -> None, or the reason it did not start
//   moy_elf.poll() -> [(pid, status)] of the services that ended by themselves
//   moy_elf.stop(pid) -> status, or -1 for no such service
//   moy_elf.info(pid) -> (threads, handles, calls, timers, events) or None
//   moy_elf.stats() -> (started, swept, killed, leaked)
//   moy_elf.mem() -> (internal free, internal largest, psram free, psram largest)
//   moy_elf.load() -> (read_us, check_us, reloc_us, internal free before, after):
//       the last load's cost, which the calling task waits out
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
#include "py/builtin.h"

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "sdkconfig.h"

#include "esp_elf.h"
#include "moy_elf.h"
#include "moy_ota.h"
#include "moy_os.h"
#include "moy_sh.h"

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

// The last load's cost: the file's read, its signature and verdict, its
// relocation (the loader's cache flush included), all on the calling task;
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

static void unload(void *loaded) {
    esp_elf_t *elf = loaded;
    esp_elf_deinit(elf);
    heap_caps_free(elf);
}

// The file at `path` read, checked and relocated into *m: 0, or -1 with the
// reason in err. Raises only when the file cannot be read.
static int load(mp_obj_t path, moy_os_module_t *m, char *err, size_t errlen) {
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
    const char *bad = moy_ota_verify_module(buf, len, MOY_ELF_SIG_SCHEME, CHIP, &len);
    if (bad != NULL && strcmp(bad, MOY_OTA_MODULE_UNSIGNED) != 0) {
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
    int64_t t2 = esp_timer_get_time();
    esp_elf_t *elf = heap_caps_calloc(1, sizeof *elf, PSRAM_CAPS);
    if (elf == NULL) {
        heap_caps_free(buf);
        snprintf(err, errlen, "out of memory");
        return -1;
    }
    esp_elf_init(elf);
    elf_set_symbol_resolver(resolve_none);
    int r = esp_elf_relocate(elf, buf, len);
    heap_caps_free(buf);
    if (r != 0) {
        esp_elf_deinit(elf);
        heap_caps_free(elf);
        snprintf(err, errlen, "load: the module would not relocate (%d)", r);
        return -1;
    }
    esp_elf_request(elf, 0, 0, NULL);
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
    moy_os_module_t m;
    if (load(args[0], &m, err, sizeof err) != 0) {
        return result(MOY_OS_REFUSED, err);
    }
    const char **argv = m_new(const char *, argc ? argc : 1);
    for (size_t i = 0; i < argc; i++) {
        argv[i] = mp_obj_str_get_str(items[i]);
    }
    int status = moy_os_command(s, &m, NULL, (int)argc, argv, roots, pid, id, bg_pid, budget,
                                err, sizeof err);
    m_del(const char *, argv, argc ? argc : 1);
    return result(status, err);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_command_obj, 7, 7, mod_command);

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
    moy_os_module_t m;
    if (load(args[0], &m, err, sizeof err) != 0) {
        return result(MOY_OS_REFUSED, err);
    }
    const char **argv = m_new(const char *, argc ? argc : 1);
    for (size_t i = 0; i < argc; i++) {
        argv[i] = mp_obj_str_get_str(items[i]);
    }
    int status = moy_os_command_start(s, &m, NULL, (int)argc, argv, roots, pid, id, 0, budget,
                                      err, sizeof err);
    m_del(const char *, argv, argc ? argc : 1);
    return result(status, err);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mod_start_obj, 6, 6, mod_start);

static mp_obj_t mod_step(mp_obj_t pid, mp_obj_t max_ms) {
    return mp_obj_new_int(moy_os_command_step((uint32_t)mp_obj_get_int(pid),
                                              (uint32_t)mp_obj_get_int(max_ms) * 1000u));
}
static MP_DEFINE_CONST_FUN_OBJ_2(mod_step_obj, mod_step);

static mp_obj_t mod_cancel(mp_obj_t pid) {
    return mp_obj_new_int(moy_os_command_stop((uint32_t)mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_cancel_obj, mod_cancel);

static mp_obj_t mod_service(mp_obj_t path, mp_obj_t pid, mp_obj_t id) {
    char err[ERR_MAX];
    moy_os_module_t m;
    if (load(path, &m, err, sizeof err) != 0) {
        return mp_obj_new_str(err, strlen(err));
    }
    if (moy_os_service(&m, NULL, (uint32_t)mp_obj_get_int(pid), mp_obj_str_get_str(id), err,
                       sizeof err) == NULL) {
        return mp_obj_new_str(err, strlen(err));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_3(mod_service_obj, mod_service);

static mp_obj_t mod_poll(void) {
    uint32_t pids[MOY_OS_PROCS];
    int32_t codes[MOY_OS_PROCS];
    uint32_t n = moy_os_poll(pids, codes, MOY_OS_PROCS);
    mp_obj_t out = mp_obj_new_list(0, NULL);
    for (uint32_t i = 0; i < n; i++) {
        mp_obj_t t[2] = { mp_obj_new_int_from_uint(pids[i]), mp_obj_new_int(codes[i]) };
        mp_obj_list_append(out, mp_obj_new_tuple(2, t));
    }
    return out;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mod_poll_obj, mod_poll);

static mp_obj_t mod_stop(mp_obj_t pid) {
    return mp_obj_new_int(moy_os_stop((uint32_t)mp_obj_get_int(pid)));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_stop_obj, mod_stop);

static mp_obj_t mod_info(mp_obj_t pid) {
    moy_os_info_t i;
    if (moy_os_info((uint32_t)mp_obj_get_int(pid), &i) != 0) {
        return mp_const_none;
    }
    mp_obj_t t[5] = {
        mp_obj_new_int_from_uint(i.threads), mp_obj_new_int_from_uint(i.handles),
        mp_obj_new_int_from_uint(i.calls), mp_obj_new_int_from_uint(i.timers),
        mp_obj_new_int_from_uint(i.events),
    };
    return mp_obj_new_tuple(5, t);
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

static const mp_rom_map_elem_t moy_elf_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_moy_elf) },
    { MP_ROM_QSTR(MP_QSTR_CHIP), MP_ROM_QSTR(CHIP_QSTR) },
    { MP_ROM_QSTR(MP_QSTR_ROWS), MP_ROM_INT(MOY_OS_ROWS) },
    { MP_ROM_QSTR(MP_QSTR_VERSION), MP_ROM_INT(MOY_OS_VERSION) },
    { MP_ROM_QSTR(MP_QSTR_command), MP_ROM_PTR(&mod_command_obj) },
    { MP_ROM_QSTR(MP_QSTR_load), MP_ROM_PTR(&mod_load_obj) },
    { MP_ROM_QSTR(MP_QSTR_start), MP_ROM_PTR(&mod_start_obj) },
    { MP_ROM_QSTR(MP_QSTR_step), MP_ROM_PTR(&mod_step_obj) },
    { MP_ROM_QSTR(MP_QSTR_cancel), MP_ROM_PTR(&mod_cancel_obj) },
    { MP_ROM_QSTR(MP_QSTR_service), MP_ROM_PTR(&mod_service_obj) },
    { MP_ROM_QSTR(MP_QSTR_poll), MP_ROM_PTR(&mod_poll_obj) },
    { MP_ROM_QSTR(MP_QSTR_stop), MP_ROM_PTR(&mod_stop_obj) },
    { MP_ROM_QSTR(MP_QSTR_info), MP_ROM_PTR(&mod_info_obj) },
    { MP_ROM_QSTR(MP_QSTR_stats), MP_ROM_PTR(&mod_stats_obj) },
    { MP_ROM_QSTR(MP_QSTR_mem), MP_ROM_PTR(&mod_mem_obj) },
};
static MP_DEFINE_CONST_DICT(moy_elf_globals, moy_elf_globals_table);

const mp_obj_module_t moy_elf_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&moy_elf_globals,
};

MP_REGISTER_MODULE(MP_QSTR_moy_elf, moy_elf_user_cmodule);

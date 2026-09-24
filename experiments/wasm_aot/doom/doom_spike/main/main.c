/*
 * #158 S3 spike, the Doom half: WAMR on the LilyGO T-Deck (ESP32-S3) running
 * doomgeneric compiled to wasm32 and AOT-compiled for Xtensa. The module's
 * imports are the six console verbs in dg_moy.c plus stubs for the WASI
 * calls wasi-libc drags in; the WAD is a flash partition; the frame is a
 * 320x200 indexed buffer in linear memory blitted through the palette.
 *
 * Partition "wasmaot" carries: 8-byte magic "MOYAOT\0\0", u32 length, u32
 * pad, then the .aot. A REL (plain) file is loaded from the DATA mapping and
 * its text copied to exec memory; an XIP file is executed where it lies, so
 * it is handed to the loader through the INSTRUCTION mapping.
 */
#include <stdio.h>
#include <string.h>
#include <pthread.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "soc/soc.h"
#include "esp_pthread.h"
#include "sdkconfig.h"
#include "wasm_export.h"
#include "bh_platform.h"

/* From the patched WAMR esp-idf platform (toolchain/patch_wamr_s3.py): the
 * header only declares it inside the component's own build. */
#if CONFIG_IDF_TARGET_ESP32S3
void os_register_xip_window(const void *ibus, const void *dbus, size_t size);
#endif

#include "lcd.h"
#include "input.h"

#define ZONE_MB 3
#define REPORT_EVERY 70
#define THREAD_STACK (160 * 1024)

static const uint8_t *s_wad;
static uint32_t s_wad_len;
static int64_t s_draw_us;
static uint32_t s_draws;

/* ---- the console's verbs (module "env") ---------------------------------- */

static uint32_t n_ticks_ms(wasm_exec_env_t env)
{
    (void)env;
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void n_sleep_ms(wasm_exec_env_t env, uint32_t ms)
{
    (void)env;
    vTaskDelay(pdMS_TO_TICKS(ms ? ms : 1));
}

static uint32_t n_get_key(wasm_exec_env_t env)
{
    (void)env;
    return input_pop_key();
}

static void n_draw(wasm_exec_env_t env, uint32_t frame_off, uint32_t pal_off)
{
    wasm_module_inst_t inst = wasm_runtime_get_module_inst(env);
    if (!wasm_runtime_validate_app_addr(inst, frame_off, 320 * 200)
        || !wasm_runtime_validate_app_addr(inst, pal_off, 256 * 4)) {
        wasm_runtime_set_exception(inst, "moy_draw: buffer out of bounds");
        return;
    }
    const uint8_t *frame = wasm_runtime_addr_app_to_native(inst, frame_off);
    const uint32_t *pal = wasm_runtime_addr_app_to_native(inst, pal_off);
    int64_t t0 = esp_timer_get_time();
    lcd_blit_indexed(frame, pal);
    s_draw_us += esp_timer_get_time() - t0;
    s_draws++;
}

static uint32_t n_wad_size(wasm_exec_env_t env)
{
    (void)env;
    return s_wad_len;
}

static uint32_t n_wad_read(wasm_exec_env_t env, uint32_t off, uint32_t dst_off, uint32_t len)
{
    wasm_module_inst_t inst = wasm_runtime_get_module_inst(env);
    if (off >= s_wad_len) {
        return 0;
    }
    if (len > s_wad_len - off) {
        len = s_wad_len - off;
    }
    if (!wasm_runtime_validate_app_addr(inst, dst_off, len)) {
        wasm_runtime_set_exception(inst, "moy_wad_read: buffer out of bounds");
        return 0;
    }
    memcpy(wasm_runtime_addr_app_to_native(inst, dst_off), s_wad + off, len);
    return len;
}

static NativeSymbol ENV_SYMS[] = {
    { "moy_ticks_ms", n_ticks_ms, "()i", NULL },
    { "moy_sleep_ms", n_sleep_ms, "(i)", NULL },
    { "moy_get_key", n_get_key, "()i", NULL },
    { "moy_draw", n_draw, "(ii)", NULL },
    { "moy_wad_size", n_wad_size, "()i", NULL },
    { "moy_wad_read", n_wad_read, "(iii)i", NULL },
};

/* ---- what wasi-libc imports, answered as "no filesystem, stdout is serial"
 *
 * The signature strings are WAMR's own for these names
 * (core/iwasm/libraries/libc-wasi/libc_wasi_wrapper.c) and that is not a
 * style choice: wamrc knows the WASI imports at compile time and emits the
 * app-offset -> native-pointer conversion for every '*' INTO the module, so
 * a stub declared "(iiii)i" receives native pointers it then rejects as out
 * of bounds. Pointer arguments below are already native; only what they
 * point at (an iovec's buffer offset) still needs converting. */

#define WASI_EBADF  8
#define WASI_ENOENT 44

static uint32_t w_fd_write(wasm_exec_env_t env, uint32_t fd, const uint32_t *iov,
                           uint32_t iovs_len, uint32_t *nwritten)
{
    wasm_module_inst_t inst = wasm_runtime_get_module_inst(env);
    uint32_t total = 0;
    for (uint32_t i = 0; i < iovs_len; i++) {
        uint32_t buf = iov[i * 2], len = iov[i * 2 + 1];
        if (!wasm_runtime_validate_app_addr(inst, buf, len)) {
            return WASI_EBADF;
        }
        if (fd == 1 || fd == 2) {
            fwrite(wasm_runtime_addr_app_to_native(inst, buf), 1, len, stdout);
        }
        total += len;
    }
    *nwritten = total;
    return 0;
}

static uint32_t w_fd_read(wasm_exec_env_t env, uint32_t fd, const uint32_t *iov,
                          uint32_t iovs_len, uint32_t *nread)
{
    (void)env; (void)fd; (void)iov; (void)iovs_len; (void)nread;
    return WASI_EBADF;
}

static uint32_t w_fd_seek(wasm_exec_env_t env, uint32_t fd, int64_t off, uint32_t whence,
                          uint64_t *newoff)
{
    (void)env; (void)fd; (void)off; (void)whence; (void)newoff;
    return WASI_EBADF;
}

static uint32_t w_fd_close(wasm_exec_env_t env, uint32_t fd)
{
    (void)env; (void)fd;
    return WASI_EBADF;
}

static uint32_t w_fd_fdstat_get(wasm_exec_env_t env, uint32_t fd, uint8_t *st)
{
    (void)env;
    if (fd > 2) {
        return WASI_EBADF;
    }
    memset(st, 0, 24);
    st[0] = 2;   /* character device */
    return 0;
}

static uint32_t w_fd_fdstat_set_flags(wasm_exec_env_t env, uint32_t fd, uint32_t flags)
{
    (void)env; (void)fd; (void)flags;
    return WASI_EBADF;
}

static uint32_t w_fd_prestat_get(wasm_exec_env_t env, uint32_t fd, uint8_t *prestat)
{
    (void)env; (void)fd; (void)prestat;
    return WASI_EBADF;
}

static uint32_t w_fd_prestat_dir_name(wasm_exec_env_t env, uint32_t fd, char *path, uint32_t len)
{
    (void)env; (void)fd; (void)path; (void)len;
    return WASI_EBADF;
}

static uint32_t w_path_open(wasm_exec_env_t env, uint32_t dirfd, uint32_t dirflags,
                            const char *path, uint32_t path_len, uint32_t oflags,
                            int64_t rights, int64_t rights_inh, uint32_t fdflags,
                            uint32_t *fd_out)
{
    (void)env; (void)dirfd; (void)dirflags; (void)path; (void)path_len; (void)oflags;
    (void)rights; (void)rights_inh; (void)fdflags; (void)fd_out;
    return WASI_ENOENT;
}

static uint32_t w_path3(wasm_exec_env_t env, uint32_t fd, const char *path, uint32_t len)
{
    (void)env; (void)fd; (void)path; (void)len;
    return WASI_ENOENT;
}

static uint32_t w_path_rename(wasm_exec_env_t env, uint32_t ofd, const char *opath,
                              uint32_t olen, uint32_t nfd, const char *npath, uint32_t nlen)
{
    (void)env; (void)ofd; (void)opath; (void)olen; (void)nfd; (void)npath; (void)nlen;
    return WASI_ENOENT;
}

static void w_proc_exit(wasm_exec_env_t env, uint32_t code)
{
    printf("DOOM proc_exit code=%u\n", (unsigned)code);
    wasm_runtime_set_exception(wasm_runtime_get_module_inst(env), "proc_exit");
}

static NativeSymbol WASI_SYMS[] = {
    { "fd_write", w_fd_write, "(i*i*)i", NULL },
    { "fd_read", w_fd_read, "(i*i*)i", NULL },
    { "fd_seek", w_fd_seek, "(iIi*)i", NULL },
    { "fd_close", w_fd_close, "(i)i", NULL },
    { "fd_fdstat_get", w_fd_fdstat_get, "(i*)i", NULL },
    { "fd_fdstat_set_flags", w_fd_fdstat_set_flags, "(ii)i", NULL },
    { "fd_prestat_get", w_fd_prestat_get, "(i*)i", NULL },
    { "fd_prestat_dir_name", w_fd_prestat_dir_name, "(i*~)i", NULL },
    { "path_open", w_path_open, "(ii*~iIIi*)i", NULL },
    { "path_create_directory", w_path3, "(i*~)i", NULL },
    { "path_remove_directory", w_path3, "(i*~)i", NULL },
    { "path_unlink_file", w_path3, "(i*~)i", NULL },
    { "path_rename", w_path_rename, "(i*~i*~)i", NULL },
    { "proc_exit", w_proc_exit, "(i)", NULL },
};

/* ---- the run ------------------------------------------------------------- */

static void heap_line(const char *when)
{
    printf("DOOM %s internal=%u largest_internal=%u exec=%u largest_exec=%u spiram=%u "
           "largest_spiram=%u\n",
           when,
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_EXEC),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_EXEC),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM));
}

static const uint8_t *map_partition(const char *label, esp_partition_mmap_memory_t how,
                                    uint32_t offset, uint32_t len, uint32_t *out_len)
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, 0xff, label);
    if (!part) {
        printf("DOOM ERR no partition %s\n", label);
        return NULL;
    }
    if (!len) {
        len = part->size - offset;
    }
    const void *ptr = NULL;
    esp_partition_mmap_handle_t h;
    esp_err_t e = esp_partition_mmap(part, offset, len, how, &ptr, &h);
    if (e != ESP_OK) {
        printf("DOOM ERR mmap %s (%s): 0x%x\n", label, how == ESP_PARTITION_MMAP_INST ? "inst" : "data",
               (unsigned)e);
        return NULL;
    }
    if (out_len) {
        *out_len = len;
    }
    return ptr;
}

static bool call_i(wasm_module_inst_t inst, wasm_exec_env_t env, const char *fn, uint32_t arg)
{
    wasm_function_inst_t f = wasm_runtime_lookup_function(inst, fn);
    if (!f) {
        printf("DOOM ERR no export %s\n", fn);
        return false;
    }
    uint32_t argv[1] = { arg };
    if (!wasm_runtime_call_wasm(env, f, 1, argv)) {
        printf("DOOM ERR %s: %s\n", fn, wasm_runtime_get_exception(inst));
        wasm_runtime_dump_call_stack(env);
        return false;
    }
    return true;
}

static void *doom_main(void *arg)
{
    (void)arg;
    heap_line("boot");

    RuntimeInitArgs init_args;
    memset(&init_args, 0, sizeof(init_args));
    init_args.mem_alloc_type = Alloc_With_Allocator;
    init_args.mem_alloc_option.allocator.malloc_func = (void *)os_malloc;
    init_args.mem_alloc_option.allocator.realloc_func = (void *)os_realloc;
    init_args.mem_alloc_option.allocator.free_func = (void *)os_free;
    if (!wasm_runtime_full_init(&init_args)) {
        printf("DOOM ERR runtime init\n");
        return NULL;
    }
    wasm_runtime_register_natives("env", ENV_SYMS, sizeof(ENV_SYMS) / sizeof(ENV_SYMS[0]));
    wasm_runtime_register_natives("wasi_snapshot_preview1", WASI_SYMS,
                                  sizeof(WASI_SYMS) / sizeof(WASI_SYMS[0]));

    /* The WAD: "IWAD" + the file, straight off the flash cache. */
    const uint8_t *wad = map_partition("wad", ESP_PARTITION_MMAP_DATA, 0, 0, NULL);
    if (!wad || memcmp(wad, "IWAD", 4) != 0) {
        printf("DOOM ERR wad partition has no IWAD header\n");
        return NULL;
    }
    s_wad = wad;
    /* WAD header: magic, numlumps, infotableofs; the directory ends the file */
    uint32_t numlumps = ((const uint32_t *)wad)[1], infotab = ((const uint32_t *)wad)[2];
    s_wad_len = infotab + numlumps * 16;
    printf("DOOM wad bytes=%u lumps=%u\n", (unsigned)s_wad_len, (unsigned)numlumps);

    /* The module: header, then the file through the mapping its type needs. */
    const uint8_t *hdr = map_partition("wasmaot", ESP_PARTITION_MMAP_DATA, 0, 4096, NULL);
    if (!hdr || memcmp(hdr, "MOYAOT\0\0", 8) != 0) {
        printf("DOOM ERR wasmaot partition has no MOYAOT header\n");
        return NULL;
    }
    uint32_t aot_len = ((const uint32_t *)hdr)[2];
    const uint8_t *data_ptr = map_partition("wasmaot", ESP_PARTITION_MMAP_DATA, 16, aot_len, NULL);
    if (!data_ptr) {
        return NULL;
    }
    bool is_wasm = memcmp(data_ptr, "\0asm", 4) == 0;
    uint16_t e_type = is_wasm ? 0 : *(const uint16_t *)(data_ptr + 20);
    const uint8_t *buf = data_ptr;
    const char *mode = is_wasm ? "interp" : (e_type == 4 ? "aot-xip" : "aot");
    if (is_wasm) {
        /* the interpreter's loader patches bytecode in place: give it RAM */
        uint8_t *copy = heap_caps_malloc(aot_len, MALLOC_CAP_SPIRAM);
        if (!copy) {
            printf("DOOM ERR no PSRAM for a %u-byte module copy\n", (unsigned)aot_len);
            return NULL;
        }
        memcpy(copy, data_ptr, aot_len);
        buf = copy;
    }
    if (e_type == 4) {
        /* The S3's instruction alias is fetch-only: the loader reads the file
         * through the DATA mapping, the (patched) runtime fetches through the
         * INST one -- os_register_xip_window ties the two together. */
        /* One MMU table serves both buses on the S3, so mapping the same
         * pages twice hands back one address: the instruction alias of a
         * DATA mapping is a fixed offset away. */
#if CONFIG_IDF_TARGET_ESP32S3
        const uint8_t *inst = data_ptr + (SOC_IROM_LOW - SOC_DROM_LOW);
        os_register_xip_window(inst, data_ptr, aot_len);
        printf("DOOM xip inst=%p data=%p\n", inst, data_ptr);
#else
        /* the P4's bus is unified: the DATA mapping is fetchable as it is */
        printf("DOOM xip at %p (unified bus)\n", data_ptr);
#endif
    }
    printf("DOOM module mode=%s bytes=%u at %p\n", mode, (unsigned)aot_len, buf);

    char err[160];
    int64_t t0 = esp_timer_get_time();
    wasm_module_t module = wasm_runtime_load((uint8_t *)buf, aot_len, err, sizeof(err));
    if (!module) {
        printf("DOOM ERR load: %s\n", err);
        return NULL;
    }
    wasm_module_inst_t inst = wasm_runtime_instantiate(module, 64 * 1024, 0, err, sizeof(err));
    if (!inst) {
        printf("DOOM ERR instantiate: %s\n", err);
        return NULL;
    }
    wasm_exec_env_t env = wasm_runtime_create_exec_env(inst, 96 * 1024);
    if (!env) {
        printf("DOOM ERR exec env\n");
        return NULL;
    }
    printf("DOOM loaded mode=%s in %lld ms\n", mode, (long long)((esp_timer_get_time() - t0) / 1000));
    {
        wasm_memory_inst_t mem = wasm_runtime_get_default_memory(inst);
        printf("DOOM memory pages=%u bytes_per_page=%u\n",
               mem ? (unsigned)wasm_memory_get_cur_page_count(mem) : 0,
               mem ? (unsigned)wasm_memory_get_bytes_per_page(mem) : 0);
    }
    heap_line("loaded");

    lcd_init();
    input_init();

    t0 = esp_timer_get_time();
    if (!call_i(inst, env, "dg_start", ZONE_MB)) {
        return NULL;
    }
    printf("DOOM started in %lld ms\n", (long long)((esp_timer_get_time() - t0) / 1000));
    heap_line("running");

    uint32_t frames = 0;
    int64_t win_start = esp_timer_get_time(), tick_us = 0, tick_max = 0;
    for (;;) {
        input_poll();
        int64_t a = esp_timer_get_time();
        if (!call_i(inst, env, "dg_tick", 0)) {
            return NULL;
        }
        int64_t dt = esp_timer_get_time() - a;
        tick_us += dt;
        if (dt > tick_max) {
            tick_max = dt;
        }
        if (++frames % REPORT_EVERY == 0) {
            int64_t now = esp_timer_get_time();
            double secs = (now - win_start) / 1e6;
            printf("DOOM mode=%s frames=%u fps=%.1f tick_avg=%.1fms tick_max=%.1fms "
                   "draw_avg=%.1fms draws=%u\n",
                   mode, (unsigned)frames, REPORT_EVERY / secs,
                   tick_us / 1000.0 / REPORT_EVERY, tick_max / 1000.0,
                   s_draws ? s_draw_us / 1000.0 / s_draws : 0.0, (unsigned)s_draws);
            win_start = now;
            tick_us = tick_max = 0;
            s_draw_us = 0;
            s_draws = 0;
        }
    }
}

void app_main(void)
{
    vTaskDelay(pdMS_TO_TICKS(3000));   /* the S3 re-enumerates after reset; let the host attach */

    esp_pthread_cfg_t cfg = esp_pthread_get_default_config();
    cfg.stack_size = THREAD_STACK;
    cfg.pin_to_core = 1;
    cfg.prio = 5;
    esp_pthread_set_cfg(&cfg);

    pthread_t tid;
    if (pthread_create(&tid, NULL, doom_main, NULL) != 0) {
        printf("DOOM ERR pthread_create\n");
        return;
    }
    pthread_join(tid, NULL);
    printf("DOOM exit\n");
}

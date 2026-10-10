// moy_play_stop: the run the kernel stops the VM for
// (docs/kernel_cartpath_2026-10.md section 5), on a board that takes the stop.
//
// THE LEVER is the board's MOY_VM_STOP (its mpconfigboard.h): the S3 boards,
// whose PSRAM a Doom-sized compiled cart needs whole. A board without it (the
// P4s) has no verdict here and keeps the VM for every run (KEEP_LEVER).
//
// THE VERDICT (moy_play_stop_verdict, at the launch, the VM up): a VM-free
// game stops the VM only when every clause of section 5.1 lets it -- its
// route is HOME, no Python owner holds a WiFi lease (the link and a cart's
// run are the kernel's own holders), no update is being written, the kernel's
// present shows its canvas -- and, the policy being `need`, its fit check
// FAILS with the VM up: a compiled cart's footprint (moy_wasm_footprint.h,
// the module this console would load) against free PSRAM and its largest
// block. `vmstop force` (the dev channel's, never a kid's) makes the fit read
// as failing, so a board where the cart fits can still be made to stop.
//
// THE RUN (moy_play_stopped_run), on the VM service task after the stop's
// mp_deinit (native/moy_kernel's moy_kernel_vm_down): the PSRAM line the gate
// reads, then the load -- the fit checked again against the largest block,
// the module found by name (this console's AOT module, else main.wasm on the
// interpreter, as device/moycore_glue.py's WasmRun finds it), main.wasm's head
// and sha256, the config as cfg() reads it, pmem from pmem.json -- through
// moycore's C open over buffers held here, the canvas the panel's own back
// buffer. The run is then the loop's foreground (moy_play_front) and the
// kernel's loop steps until it ends: the hold, quit(), a Ctrl-C (SERIAL), the
// dev channel's `end`. A refusal or a raise paints the chrome's panel and
// waits for a press. pmem is written back (the store's crash-safe write) and
// everything is closed before the next VM starts.

#include "py/mpconfig.h"

#if defined(__has_include)
#if __has_include("esp_heap_caps.h") && defined(MOY_VM_STOP) && MOY_VM_STOP \
    && defined(MOY_KERNEL_PANEL) && MOY_WASM

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "py/mpstate.h"
#include "py/gc.h"
#include "sdkconfig.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"

#include "moy_cat.h"
#include "moy_chrome.h"
#include "moy_fs.h"
#include "moy_input.h"
#include "moy_json.h"
#include "moy_loop.h"
#include "moy_play.h"
#include "moy_route.h"
#include "moy_tick.h"
#include "moy_vol.h"
#include "moycore_lua.h"
#include "moycore_run.h"
#include "../moy_wasm/moy_wasm_footprint.h"
#include "../moy_wasm/moy_wasm_key.h"

#if __has_include("../moy_audio/moy_aud.h")
#include "../moy_audio/moy_aud.h"
#define STOP_AUDIO 1
#endif

#if CONFIG_IDF_TARGET_ESP32S3
#define STOP_CHIP "esp32s3"
#elif CONFIG_IDF_TARGET_ESP32P4
#define STOP_CHIP "esp32p4"
#endif

#define PSRAM_CAPS (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)
#define MAX_PAGES 16384u            // device/moycore_glue.py's _MAX_PAGES
#define AUDIO_MAX 32                // MoycoreRun.AUDIO_MAX
#define HEAD_MAX (256u * 1024u)     // main.wasm's head: its sections to memory
#define CFG_MAX (16u * 1024u)
#define PMEM_MAX (8u * 1024u)
#define END_SERIAL 5                // MOY_PLAY_END_SERIAL

// The kernel's (native/moy_kernel): the heaps line, the serial line out, the
// task watchdog.
void moy_kernel_heaps(const char *tag, const char *when, const char *extra);
void moy_kernel_say(const char *line);
void moy_kernel_feed(void);
void moy_kernel_stamp(int part);
bool moy_loop_board_serial(void);
// The updater's state (native/moy_net), where the image has it.
typedef struct {
    uint8_t phase;
    uint8_t sink;
    uint32_t dl_done, dl_total;
    uint32_t done, total;
    char err[96];
} stop_ota_state_t;
void moy_ota_state(stop_ota_state_t *out) __attribute__((weak));

// The panel's kernel entry points (the board's MOY_KERNEL_PANEL module): its
// C-owned framebuffers, shipped by the board's flush feeder. Strong: a weak
// reference keeps nothing alive through the linker's section collection.
int MOY_KERNEL_PANEL(nfbs)(void);
uint8_t *MOY_KERNEL_PANEL(fbn)(int n);
int MOY_KERNEL_PANEL(kick)(int n);
bool MOY_KERNEL_PANEL(wait)(void);

static bool s_force;

void moy_play_stop_force(bool on) {
    s_force = on;
}

bool moy_play_stop_forced(void) {
    return s_force;
}

// -- the module a compiled cart loads -------------------------------------------

// `<path>/<main stem>.<chip>.f<format>.aot`: device/moycore_glue.py's aot_path.
static void aot_path(char *out, size_t n, const char *path, const char *main, size_t mn) {
    if (mn > 5 && memcmp(main + mn - 5, ".wasm", 5) == 0) {
        mn -= 5;
    }
    snprintf(out, n, "%s/%.*s.%s.f%s.aot", path, (int)mn, main, STOP_CHIP,
             MOY_WASM_FORMAT_VERSION);
}

static int stat_size(const char *p, uint32_t *size) {
    moy_vol_t v;
    const char *rest;
    moy_vol_stat_t st;
    int gated = moy_vol_gate_enter(p);
    int e = moy_vol_at(p, &v, &rest);
    if (e == 0) {
        e = moy_vol_stat(&v, rest, &st);
    }
    moy_vol_gate_leave(gated);
    if (e == 0 && st.is_dir) {
        e = MOY_EISDIR;
    }
    *size = e == 0 ? st.size : 0;
    return e;
}

typedef struct {
    char main[160];             // main.wasm's path
    char module[192];           // the file to load
    bool interp;                // main.wasm itself, on the interpreter
    uint32_t pages;
    uint32_t size;              // the module file's
} module_t;

static bool module_of(const moy_cat_entry_t *e, const char *path, module_t *m) {
    const char *main = e->main != NULL && e->main_n ? e->main : "main.wasm";
    size_t mn = e->main != NULL && e->main_n ? e->main_n : 9;
    if (e->memory.kind != 1 || e->memory.value <= 0) {
        return false;
    }
    m->pages = e->memory.value > MAX_PAGES ? MAX_PAGES : (uint32_t)e->memory.value;
    snprintf(m->main, sizeof(m->main), "%s/%.*s", path, (int)mn, main);
    aot_path(m->module, sizeof(m->module), path, main, mn);
    m->interp = stat_size(m->module, &m->size) != 0 || m->size == 0;
    if (m->interp) {
        snprintf(m->module, sizeof(m->module), "%s", m->main);
        if (stat_size(m->module, &m->size) != 0 || m->size == 0) {
            return false;
        }
    }
    return true;
}

static void footprint(const module_t *m, uint64_t *total, uint64_t *block) {
    uint64_t memory = (uint64_t)m->pages * 65536u;
    if (m->interp) {
        moy_wasm_interp_footprint(memory, m->size, total, block);
    } else {
        moy_wasm_footprint(memory, m->size, total, block);
    }
}

// -- the verdict ------------------------------------------------------------------

// The bytes the VM's heap areas hold (what a stop gives back), read with the
// VM up.
static uint64_t vm_heap_bytes(void) {
    uint64_t held = 0;
    for (mp_state_mem_area_t *a = &MP_STATE_MEM(area); a != NULL;) {
        held += (uint64_t)(a->gc_pool_end
                           - (a == &MP_STATE_MEM(area) ? a->gc_alloc_table_start : (byte *)a));
        #if MICROPY_GC_SPLIT_HEAP
        a = a->next;
        #else
        a = NULL;
        #endif
    }
    return held;
}

uint8_t moy_play_stop_verdict(const moy_cat_entry_t *e, const char *path, uint32_t flags,
                              uint32_t fit[5]) {
    if (!(flags & MOY_PLAY_HOME)) {
        return MOY_PLAY_KEEP_PLACE;
    }
    const moy_spine_kernel_t *k = moy_spine_kernel(NULL);
    if (k != NULL) {
        uint32_t kernel_holders = moy_lease_bit("link", 4) | moy_lease_bit("cart", 4);
        if (moy_leases_mask(k->leases) & ~kernel_holders) {
            return MOY_PLAY_KEEP_LEASE;
        }
    }
    if (moy_ota_state != NULL) {
        stop_ota_state_t st;
        moy_ota_state(&st);
        if (st.phase == 1 || st.phase == 3) {       // FETCHING, WRITING
            return MOY_PLAY_KEEP_OTA;
        }
    }
    if (MOY_KERNEL_PANEL(fbn)(0) == NULL || MOY_KERNEL_PANEL_ROT != 0
        || (e->canvas != 0 && (e->canvas_w != MOY_KERNEL_PANEL_W
                               || e->canvas_h != MOY_KERNEL_PANEL_H))) {
        return MOY_PLAY_KEEP_FRONT;
    }
    module_t m;
    if (!e->compiled || !module_of(e, path, &m)) {
        return MOY_PLAY_KEEP_FITS;          // no footprint to check: it fits
    }
    uint64_t total, block;
    footprint(&m, &total, &block);
    size_t free_ = heap_caps_get_free_size(PSRAM_CAPS);
    size_t largest = heap_caps_get_largest_free_block(PSRAM_CAPS);
    uint64_t held = vm_heap_bytes();
    fit[0] = (uint32_t)total;
    fit[1] = (uint32_t)block;
    fit[2] = (uint32_t)free_;
    fit[3] = (uint32_t)largest;
    fit[4] = (uint32_t)held;
    if (!s_force && total <= free_ && block <= largest) {
        return MOY_PLAY_KEEP_FITS;
    }
    // What the stop gives back is at most the VM's heap: a cart that would
    // not fit even with all of it is the fit notice, with the VM up.
    if (total > free_ + held || block > largest + held) {
        return MOY_PLAY_KEEP_BIG;
    }
    return MOY_PLAY_STOPS;
}

// -- the load ----------------------------------------------------------------------

typedef struct {
    int32_t snap[SNAP_LEN];
    int32_t aq[1 + AQ_SLOTS * AUDIO_MAX];
    int32_t pmem[256];
    int32_t pmem_out[256];
    uint16_t wire[MOY_PALETTE];
    moy_tick_t tick;
    module_t m;
    char sha[65];
    char cfg[CFG_MAX];
    size_t cfg_len;
    char writable[512];
    size_t writable_len;
    uint8_t *head;
    size_t head_len;
    char title[48];
    int fps;
    bool compiled;
    bool ok;
    char err[192];
    int say;                    // the panel's own words (MOY_SAY_*), -1: the run's error
    moy_chrome_list_t chrome;
} stop_t;

static stop_t *S;

// What the run needs from its entry, read with the VM down.
static int entry_read(void *ctx, const moy_cat_entry_t *e) {
    const char *path = ctx;
    S->compiled = e->compiled != 0;
    if (!S->compiled || !module_of(e, path, &S->m)) {
        return 0;
    }
    S->fps = 0;
    int64_t fps;
    if (e->fps.v != NULL && moy_json_int(e->fps.v, e->fps.e, &fps) == 1 && fps > 0
        && fps <= 240) {
        S->fps = (int)fps;
    }
    if (e->title.v != NULL && *e->title.v == '"') {
        size_t tn = moy_json_strlen(e->title.v, e->title.e);
        if (tn < sizeof(S->title)) {
            moy_json_str(e->title.v, e->title.e, S->title);
            S->title[tn] = 0;
        }
    }
    // "writable": its strings NUL-joined, as Python joined them.
    S->writable_len = 0;
    if (e->writable_ok) {
        moy_json_iter_t it;
        const char *k, *ke, *v, *ve;
        moy_json_iter(&it, e->writable.v, e->writable.e);
        while (moy_json_next(&it, &k, &ke, &v, &ve)) {
            size_t n = moy_json_strlen(v, ve);
            if (S->writable_len + n + 1 >= sizeof(S->writable)) {
                break;
            }
            if (S->writable_len) {
                S->writable[S->writable_len++] = 0;
            }
            moy_json_str(v, ve, S->writable + S->writable_len);
            S->writable_len += n;
        }
    }
    S->ok = true;
    return 0;
}

// A file's bytes read whole into PSRAM (the caller frees), up to `cap`.
static int read_all(const char *p, size_t cap, moy_buf_t *out) {
    int gated = moy_vol_gate_enter(p);
    int e = moy_fs_read_file(p, cap, out);
    moy_vol_gate_leave(gated);
    return e;
}

// main.wasm up to the end of its memory section (device/moycore_glue.py's
// wasm_head): what moy_wasm_check reads the declared memory from.
static int read_head(const char *p) {
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f = NULL;
    int gated = moy_vol_gate_enter(p);
    int e = moy_vol_at(p, &v, &rest);
    if (e == 0) {
        e = moy_vol_open(&v, rest, MOY_VOL_READ, &f);
    }
    uint32_t size = 0;
    if (e == 0) {
        e = moy_vol_size(f, &size);
    }
    size_t want = size < HEAD_MAX ? size : HEAD_MAX;
    S->head = e == 0 ? heap_caps_malloc(want ? want : 1, PSRAM_CAPS) : NULL;
    size_t got = 0;
    if (e == 0 && S->head == NULL) {
        e = MOY_ENOMEM;
    }
    while (e == 0 && got < want) {
        size_t n = 0;
        e = moy_vol_read(f, S->head + got, want - got, &n);
        if (e == 0 && n == 0) {
            break;
        }
        got += n;
    }
    if (f != NULL) {
        moy_vol_close(f);
    }
    moy_vol_gate_leave(gated);
    if (e != 0) {
        return e;
    }
    // Sections run in id order: stop after the memory section (5), or at the
    // first section past it.
    size_t at = 8, end = got;
    if (got < 8 || memcmp(S->head, "\0asm", 4) != 0) {
        S->head_len = got;
        return 0;
    }
    while (at < end) {
        uint8_t id = S->head[at++];
        uint32_t len = 0;
        int shift = 0;
        while (at < end) {
            uint8_t b = S->head[at++];
            len |= (uint32_t)(b & 0x7F) << shift;
            shift += 7;
            if (!(b & 0x80)) {
                break;
            }
        }
        if (id > 5) {
            break;
        }
        at = at + len < end ? at + len : end;
        if (id == 5) {
            break;
        }
    }
    S->head_len = at;
    return 0;
}

static int sha_of(const char *p) {
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f = NULL;
    int gated = moy_vol_gate_enter(p);
    int e = moy_vol_at(p, &v, &rest);
    if (e == 0) {
        e = moy_vol_open(&v, rest, MOY_VOL_READ, &f);
    }
    uint8_t *buf = e == 0 ? heap_caps_malloc(16384, PSRAM_CAPS) : NULL;
    if (e == 0 && buf == NULL) {
        e = MOY_ENOMEM;
    }
    mbedtls_sha256_context c;
    mbedtls_sha256_init(&c);
    mbedtls_sha256_starts(&c, 0);
    while (e == 0) {
        size_t n = 0;
        e = moy_vol_read(f, buf, 16384, &n);
        if (e != 0 || n == 0) {
            break;
        }
        mbedtls_sha256_update(&c, buf, n);
        moy_kernel_feed();
    }
    uint8_t d[32];
    mbedtls_sha256_finish(&c, d);
    mbedtls_sha256_free(&c);
    heap_caps_free(buf);
    if (f != NULL) {
        moy_vol_close(f);
    }
    moy_vol_gate_leave(gated);
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        S->sha[2 * i] = hex[d[i] >> 4];
        S->sha[2 * i + 1] = hex[d[i] & 15];
    }
    S->sha[64] = 0;
    return e;
}

// config.json as cfg() reads it (runtime/lua_ext.py's cfg_blob): sorted by
// key, a string without its quotes, a boolean as 1/0, an int as Python's %d,
// another number as %.7g; anything else is no value.
typedef struct {
    const char *k, *ke, *v, *ve;
} cfg_row_t;

static int cfg_cmp(const void *a, const void *b) {
    const cfg_row_t *x = a, *y = b;
    char kx[64], ky[64];
    size_t nx = moy_json_strlen(x->k, x->ke), ny = moy_json_strlen(y->k, y->ke);
    if (nx >= sizeof(kx) || ny >= sizeof(ky)) {
        return nx < ny ? -1 : nx > ny;
    }
    moy_json_str(x->k, x->ke, kx);
    moy_json_str(y->k, y->ke, ky);
    size_t n = nx < ny ? nx : ny;
    int c = memcmp(kx, ky, n);
    return c ? c : (nx < ny ? -1 : nx > ny);
}

static void cfg_load(const char *path) {
    char p[224];
    snprintf(p, sizeof(p), "%s/config.json", path);
    moy_buf_t b = {0};
    S->cfg_len = 0;
    if (read_all(p, CFG_MAX, &b) != 0) {
        return;
    }
    cfg_row_t rows[64];
    int n = 0;
    moy_json_iter_t it;
    const char *s = b.p, *end = b.p + b.n;
    s = moy_json_ws(s, end);
    if (s < end && *s == '{') {
        moy_json_iter(&it, s, end);
        cfg_row_t r;
        while (n < 64 && moy_json_next(&it, &r.k, &r.ke, &r.v, &r.ve)) {
            rows[n++] = r;
        }
    }
    qsort(rows, (size_t)n, sizeof(rows[0]), cfg_cmp);
    for (int i = 0; i < n; i++) {
        char val[64];
        size_t vn = 0;
        int kind = moy_json_kind(rows[i].v, rows[i].ve);
        int64_t iv;
        if (kind == MOY_JSON_TRUE || kind == MOY_JSON_FALSE) {
            vn = (size_t)snprintf(val, sizeof(val), "%d", kind == MOY_JSON_TRUE);
        } else if (kind == MOY_JSON_INT && moy_json_int(rows[i].v, rows[i].ve, &iv) == 1) {
            // %ld, not %lld: the boards' C library formats no long long.
            if (iv >= INT32_MIN && iv <= INT32_MAX) {
                vn = (size_t)snprintf(val, sizeof(val), "%ld", (long)iv);
            } else {
                vn = (size_t)(rows[i].ve - rows[i].v);    // as config.json spells it
                if (vn >= sizeof(val)) {
                    continue;
                }
                memcpy(val, rows[i].v, vn);
            }
        } else if (kind == MOY_JSON_FLOAT) {
            char t[48];
            size_t tn = (size_t)(rows[i].ve - rows[i].v);
            if (tn >= sizeof(t)) {
                continue;
            }
            memcpy(t, rows[i].v, tn);
            t[tn] = 0;
            vn = (size_t)snprintf(val, sizeof(val), "%.7g", strtod(t, NULL));
        } else if (kind == MOY_JSON_STR) {
            vn = moy_json_strlen(rows[i].v, rows[i].ve);
        } else {
            continue;
        }
        size_t kn = moy_json_strlen(rows[i].k, rows[i].ke);
        if (S->cfg_len + kn + vn + 2 > sizeof(S->cfg)) {
            break;
        }
        moy_json_str(rows[i].k, rows[i].ke, S->cfg + S->cfg_len);
        S->cfg_len += kn;
        S->cfg[S->cfg_len++] = 0;
        if (kind == MOY_JSON_STR) {
            moy_json_str(rows[i].v, rows[i].ve, S->cfg + S->cfg_len);
        } else {
            memcpy(S->cfg + S->cfg_len, val, vn);
        }
        S->cfg_len += vn;
        S->cfg[S->cfg_len++] = 0;
    }
    moy_buf_free(&b);
}

// pmem.json: a list of 256 signed 32-bit ints (runtime/moy_carts.py's
// load_pmem); missing or unreadable is all zero.
static void pmem_load(const char *path) {
    char p[224];
    snprintf(p, sizeof(p), "%s/pmem.json", path);
    memset(S->pmem, 0, sizeof(S->pmem));
    moy_buf_t b = {0};
    if (read_all(p, PMEM_MAX, &b) != 0) {
        return;
    }
    const char *s = moy_json_ws(b.p, b.p + b.n);
    if (s < b.p + b.n && *s == '[') {
        moy_json_iter_t it;
        const char *k, *ke, *v, *ve;
        moy_json_iter(&it, s, b.p + b.n);
        for (int i = 0; i < 256 && moy_json_next(&it, &k, &ke, &v, &ve); i++) {
            int64_t x;
            if (moy_json_int(v, ve, &x) == 1) {
                S->pmem[i] = (int32_t)(uint32_t)(uint64_t)x;
            }
        }
    }
    moy_buf_free(&b);
}

// The cells written back when the run moved them, as json.dumps writes a list.
static void pmem_save(const char *path) {
    if (!moycore_RUN.open || !moycore_run_pmem_image(&moycore_RUN.c, S->pmem_out, 256)) {
        return;
    }
    char *text = heap_caps_malloc(256 * 13 + 4, PSRAM_CAPS);
    if (text == NULL) {
        return;
    }
    size_t at = 0;
    text[at++] = '[';
    for (int i = 0; i < 256; i++) {
        at += (size_t)snprintf(text + at, 16, i ? ", %ld" : "%ld", (long)S->pmem_out[i]);
    }
    text[at++] = ']';
    char p[224];
    snprintf(p, sizeof(p), "%s/pmem.json", path);
    int gated = moy_vol_gate_enter(p);
    int e = moy_fs_publish(p, text, at);
    moy_vol_gate_leave(gated);
    if (e != 0) {
        printf("STOP pmem: errno %d\n", e);
    }
    heap_caps_free(text);
}

// -- the kernel's present -----------------------------------------------------------

static int s_back;

static uint16_t *k_canvas(void *ctx, int *w, int *h) {
    (void)ctx;
    *w = MOY_KERNEL_PANEL_W;
    *h = MOY_KERNEL_PANEL_H;
    return (uint16_t *)MOY_KERNEL_PANEL(fbn)(s_back);
}

static void k_present(void *ctx, bool drew) {
    (void)ctx;
    if (!drew) {
        return;
    }
    MOY_KERNEL_PANEL(kick)(s_back);
    if (MOY_KERNEL_PANEL(nfbs)() > 1) {
        s_back ^= 1;
    }
}

static bool k_map(void *ctx, int32_t *x, int32_t *y) {
    (void)ctx;
    return *x >= 0 && *y >= 0 && *x < MOY_KERNEL_PANEL_W && *y < MOY_KERNEL_PANEL_H;
}

static const moy_front_ops_t K_FRONT = { k_canvas, k_present, k_map, NULL };

// The panel a refusal or a raise leaves, until a press or a Ctrl-C.
static void panel_wait(const char *title, const char *text, bool notice) {
    int w, h;
    uint16_t *px = k_canvas(NULL, &w, &h);
    memset(px, 0, (size_t)w * (size_t)h * 2u);
    moy_chrome_clear(&S->chrome);
    moy_chrome_panel(&S->chrome, w, h, notice, title, text, true);
    moy_chrome_raster(&S->chrome, px, w, h, S->wire, 1);
    k_present(NULL, true);
    moy_input_t *in = moy_input_kernel();
    uint32_t t0 = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
    for (;;) {
        moy_loop_step();
        if (moy_loop_board_serial()) {
            return;
        }
        uint32_t held = 0, pressed = 0;
        if (in != NULL) {
            moy_input_masks(in, MOY_INPUT_UNION, &held, &pressed);
        }
        uint32_t now = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
        if (now - t0 > 500u && (pressed || moy_input_last_key(in) != 0)) {
            return;
        }
    }
}

// The palette's 64 panel words: libmoy's canonical RGB565, byte-swapped
// where the panel's framebuffer holds them high byte first.
static void wire_init(void) {
    moy_canvas c;
    moy_pixel one;
    moy_canvas_init(&c, &one, 1, 1);
    for (int i = 0; i < MOY_PALETTE; i++) {
        uint16_t v = c.wire[i];
        S->wire[i] = MOY_KERNEL_PANEL_SWAP ? (uint16_t)((v >> 8) | (v << 8)) : v;
    }
}

// The load: 0 and the run open, or nonzero with the panel's words in S->err.
static int load(uint32_t run, const char *path, bool *notice) {
    *notice = false;
    S->ok = false;
    if (moy_cat_entry(path, entry_read, NULL, (void *)path) != 0 || !S->ok) {
        snprintf(S->err, sizeof(S->err), "%s", moy_chrome_say_text(MOY_SAY_NOLOAD));
        S->say = MOY_SAY_NOLOAD;
        return 1;
    }
    // The fit again, now the VM is gone, on the largest block: what the load
    // itself needs.
    uint64_t total, block;
    footprint(&S->m, &total, &block);
    size_t free_ = heap_caps_get_free_size(PSRAM_CAPS);
    size_t largest = heap_caps_get_largest_free_block(PSRAM_CAPS);
    if (total > free_ || block > largest) {
        moy_chrome_fit_text(S->err, sizeof(S->err), S->title, (uint32_t)total, (uint32_t)block,
                            (uint32_t)free_, (uint32_t)largest);
        *notice = true;
        return 1;
    }
    printf("STOP load: %s interp=%d size=%u pages=%u need=%u/%u\n", S->m.module,
           (int)S->m.interp, (unsigned)S->m.size, (unsigned)S->m.pages, (unsigned)total,
           (unsigned)block);
    if (read_head(S->m.main) != 0) {
        snprintf(S->err, sizeof(S->err), "refused: this cart's main.wasm is not on this "
                 "console");
        return 1;
    }
    if (!S->m.interp && sha_of(S->m.main) != 0) {
        snprintf(S->err, sizeof(S->err), "refused: main.wasm would not read");
        return 1;
    }
    cfg_load(path);
    pmem_load(path);
    S->snap[SNAP_PLAYERS] = 1;
    int w, h;
    uint16_t *fb = k_canvas(NULL, &w, &h);
    uint32_t flags = moy_play_flags();
    moycore_open_c_t o = {
        .fb = fb, .w = w, .h = h, .wire = S->wire,
        .snap = S->snap, .aq = S->aq, .aq_cap = (int)(sizeof(S->aq) / sizeof(S->aq[0])),
        .pmem = S->pmem, .cfg = S->cfg_len ? S->cfg : NULL, .cfg_len = S->cfg_len,
        .module = S->m.module, .head = S->head, .head_len = S->head_len,
        .pages = S->m.pages, .sha = S->m.interp ? NULL : S->sha, .dir = path,
        .writable = S->writable_len ? S->writable : NULL, .writable_len = S->writable_len,
        .swapped = MOY_KERNEL_PANEL_SWAP, .allow_unsigned = (flags & MOY_PLAY_UNSIGNED) != 0,
        .interp = S->m.interp,
    };
    int rc = moycore_wasm_open_c(&o, S->err, sizeof(S->err));
    if (rc != 0 && !S->m.interp && strncmp(S->err, "refused: bad signature", 22) != 0
        && strncmp(S->err, "refused: malformed signature", 28) != 0) {
        // Not tamper evidence: this console cannot use its module, and the
        // cart plays on the interpreter (WasmRun's retry).
        printf("STOP module refused: %s -- the interpreter instead\n", S->err);
        moycore_close_c();
        o.module = S->m.main;
        o.sha = NULL;
        o.interp = 1;
        rc = moycore_wasm_open_c(&o, S->err, sizeof(S->err));
    }
    (void)run;
    return rc;
}

static void run_line(const char *what) {
    char line[160];
    snprintf(line, sizeof(line), "STOP %s: %s", what, S->title[0] ? S->title : "cart");
    moy_kernel_say(line);
}

void moy_play_stopped_run(void) {
    uint32_t run = moy_play_current();
    const char *path = moy_play_path();
    if (run == 0u || path == NULL) {
        return;
    }
    char cart[192];
    snprintf(cart, sizeof(cart), "%s", path);
    // The gate's reading: PSRAM after the stop, before the load.
    moy_kernel_heaps("STOP", "down", "");
    S = heap_caps_calloc(1, sizeof(stop_t), PSRAM_CAPS);
    if (S == NULL) {
        moy_play_end(run, MOY_PLAY_END_CRASH);
        return;
    }
    wire_init();
    S->say = -1;
    moy_play_set_down(run);
    s_back = 0;
    moy_play_front_ops(&K_FRONT);
    uint32_t audio = 0;
    #ifdef STOP_AUDIO
    int arc = moy_aud_open(&audio, run, NULL, 0);
    if (arc == MOY_AUD_OK || arc == MOY_AUD_BANK) {
        moy_aud_focus(audio);
    } else {
        audio = 0;
    }
    #endif
    bool notice = false;
    int rc = load(run, cart, &notice);
    if (S->fps > 0 && (moy_play_flags() & MOY_PLAY_PACED)) {
        moy_tick_start(&S->tick, S->fps, true);
    }
    if (rc == 0) {
        moy_play_bind(run, moy_input_kernel(), audio,
                      (moy_play_flags() & MOY_PLAY_PACED) && S->fps > 0 ? &S->tick : NULL);
        rc = moy_play_open(run) == MOY_PLAY_OK && moy_play_front(run) == MOY_PLAY_OK ? 0 : 1;
        if (rc != 0) {
            moy_play_info_t i;
            moy_play_info(run, &i);
            snprintf(S->err, sizeof(S->err), "%s", i.raised ? i.error : "the run would not start");
        }
    }
    if (rc == 0) {
        run_line("running with the VM down");
        while (moy_play_front_live()) {
            moy_loop_step();
        }
        moy_play_info_t i;
        if (moy_play_current() == run && moy_play_info(run, &i) == MOY_PLAY_OK) {
            // The front gave the frame back with the run live: a raise, or what
            // only a console composes. Either ends the run here.
            snprintf(S->err, sizeof(S->err), "%s", i.raised ? i.error
                     : moy_chrome_say_text(MOY_SAY_CONSOLE));
            if (!i.raised) {
                S->say = MOY_SAY_CONSOLE;
            }
            rc = 1;
        }
    }
    bool serial = moy_loop_board_serial();
    if (rc != 0 && moy_play_current() == run) {
        moy_play_end(run, serial ? END_SERIAL : MOY_PLAY_END_CRASH);
        run_line("refused or raised");
        printf("STOP error: %s\n", S->err);
        if (!serial) {
            char title[64];
            if (notice) {
                moy_chrome_title(title, sizeof(title), MOY_SAY_FIT, 0);
            } else if (S->say >= 0) {
                moy_chrome_title(title, sizeof(title), S->say, 0);
            } else {
                moy_chrome_crash_title(title, sizeof(title), S->err, 0);
            }
            panel_wait(title, S->err, notice);
        }
    }
    // The exit's first moment, which the return start is measured from.
    moy_kernel_stamp(0);                // MOY_STAMP_EXIT
    pmem_save(cart);
    moycore_close_c();
    #ifdef STOP_AUDIO
    if (audio) {
        moy_aud_close(audio);
    }
    #endif
    if (moy_play_current() == run) {
        moy_play_end(run, MOY_PLAY_END_MENU);
    }
    moy_play_front_ops(NULL);
    MOY_KERNEL_PANEL(wait)();
    heap_caps_free(S->head);
    heap_caps_free(S);
    S = NULL;
    moy_kernel_heaps("STOP", "ended", "");
}

#endif
#endif

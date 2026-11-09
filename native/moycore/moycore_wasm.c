// moycore's compiled cart: a wasm cart's run on this console, its files, the
// engine's callbacks, and the module's verbs for it (`wasm_open`,
// `files_keep`, `frame`, ...).

#include "modmoycore_int.h"
#include "moy_proc.h"

// -- the compiled cart (docs/history/wasm_tier_plan_2026-09.md, phase 3) -----------
//
// A "runtime": "wasm" cart runs on THIS console -- the same canvas, snapshot,
// audio queue, pmem and flags a Lua cart has -- with libmoy's wasm binding
// (libmoy/moy_wasm.c, vendored) as its import table in place of a Lua state.
// The ENGINE is native/moy_wasm: the runtime, the load, the provenance key and
// the thread everything WAMR does runs on (moy_wasm_session.h). This half binds
// and nothing else: it registers the table, checks the module's shape against
// the manifest, binds the instance to the console, and calls the three hooks --
// each on the engine's thread, through the callbacks below.
//
// Every host callback the binding reaches from that thread is C, and none
// calls Python: a read or write against the console, the config table C
// holds, and the cart's files -- `read` and the written files, moy_files' C
// over the kernel's volume -- which run on the VM's task through
// moy_wasm_on_vm while it waits on the call.
// `snd` goes to the speaker's mixer where the board has one (moy_audio's
// stream, MOY_AUDIO_SND); where it has none the binding drains it by the clock.
//
// Compiled only when an engine is in the build: the boards' (native/moy_wasm,
// WAMR -- its cmake defines MOY_WASM) or the browser's (native/moy_wasm_web,
// the page's own WebAssembly engine -- its Makefile fragment defines
// MOY_WASM_JS). Both implement the same session surface; where libmoy's
// binding differs by engine (moy_wasm.h) -- registering the table, checking a
// loaded module, binding, calling a hook, par's lanes, the frame hand-off --
// so do the callbacks below, and nothing else does. The MicroPython surface
// below is unconditional so qstr scanning, which does not see those defines,
// finds every name.

enum { WCALL_INIT = 0, WCALL_UPDATE, WCALL_DRAW };

#if MOYCORE_WASM

// A compiled cart's run state, its run's row's (`wr`): several compiled runs
// are several rows, each with its engine session, and every call here reads
// the CURRENT row's. Allocated per run, from PSRAM where there is any:
// libmoy's per-run struct alone is about 4 KB (eight layer canvases), and as
// static data it would sit in internal SRAM for the whole boot -- the
// resource the S3 boards gate the tier on.
typedef struct moycore_wrun {
    moy_wasm w;                  // libmoy's per-run state for the table
    void *sess;                  // its engine session (moy_wasm_session_current)
    int take;                    // take_frames' TAKE_* for this run
    int tl;                      // the board's timed lanes are this run's
    int dead;                    // trapped: never called again
    char dir[192];               // the cart's folder: `read`'s only root
    char keep_dir[320];          // where its written files are kept
    moy_files_t *files;          // its files' session (moy_files.h)
    char *writable;              // the manifest's "writable", libmoy's form
#if MOYCORE_APP
    moy_app_run_t app;           // the app's run its imports from the extension serve
#endif
#if MOY_WASM
    // The import table's registration storage: WAMR sorts it in place and
    // points at it until the runtime is destroyed, so it lives as long as
    // this struct does -- freed by moycore_wasm_end, after the session's teardown.
    NativeSymbol *natives;
    // The app extension's rows, registered where they are: const, generated
    // in WAMR's lookup order (moy_wasm_register_rows).
    const NativeSymbol *app_natives;
#endif
} wrun_t;

#define WR (moycore_RUN.wr)

// The app ABI's import adapter (native/moy_app/moy_app_wasm.h): the rows a
// compiled app imports from module "moybyte.app", WAMR's NativeSymbol field
// for field, and the load's grant check. Weak: an image without native/moy_app
// (the Zero) has no extension, and a module importing from it is refused as
// any import outside "moy" is.
#define APP_EXT "moybyte.app"
const NativeSymbol *moy_app_wasm_natives(uint32_t *count) __attribute__((weak));
int moy_app_wasm_admit(const struct moy_app_run *r, const uint8_t *head, size_t n, char *err,
                       size_t errlen) __attribute__((weak));

// The run a native's call is for (moy_app_wasm.h): the engine's handle on the
// call -- the instance's exec env, its custom data libmoy's binding, or in
// the browser the binding itself -- to the process row that binding is the
// engine of (moy_proc.h), or, for a run no Player launched, to the session's.
#if MOYCORE_APP
const struct moy_app_run *moy_app_wasm_run_of(void *e)
{
    if (!e) return NULL;
#if MOY_WASM
    wasm_module_inst_t inst = wasm_runtime_get_module_inst((wasm_exec_env_t)e);
    e = inst ? wasm_runtime_get_custom_data(inst) : NULL;
    if (!e) return NULL;
#endif
    if (moy_proc_of_engine(e)) return moy_proc_app_of(e);
    return WR && e == (void *)&WR->w && WR->app.g ? &WR->app : NULL;
}
#endif

// The extension's table for libmoy's check: only for a run that holds an app
// grant (the Player makes one for a compiled user app that declares the
// extension), so a cart that declares none imports only from "moy".
static uint32_t app_ext(moy_wasm_ext *x)
{
    uint32_t n = 0;
#if MOYCORE_APP
    if (moy_app_wasm_natives == NULL || !WR || WR->app.g == 0) return 0;
#else
    return 0;
#endif
    x->module = APP_EXT;
    x->rows = moy_app_wasm_natives(&n);
    x->count = n;
    return 1;
}

const uint8_t *moycore_whead;   // the canonical .wasm's head, for the check
size_t moycore_whead_len;
uint32_t moycore_wpages;        // the manifest's "memory"

static void *wmem_calloc(size_t n, size_t size)
{
#ifdef MOYCORE_PSRAM
    void *p = heap_caps_calloc(n, size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (p) return p;
#endif
    return calloc(n, size);
}

static void wmem_free(void *p)
{
#ifdef MOYCORE_PSRAM
    heap_caps_free(p);
#else
    free(p);
#endif
}

static void files_close(void);

static void wrun_free(wrun_t *r)
{
    if (r == WR) files_close();
#if MOY_WASM
    wmem_free(r->natives);
#endif
    wmem_free(r->writable);
    wmem_free(r);
}

// What the board holds one of -- the speaker's `snd` stream (1) and the
// timed lanes (2) -- is the newest compiled run's: the run taking it takes
// it from every other row's.
static __attribute__((unused)) void others_lose(int what)
{
    for (int i = 0; i < MOYCORE_RUNS; i++) {
        moycore_run *r = moycore_run_at(i);
        if (r == NULL || r == moycore_run_row || r->wr == NULL) continue;
        if (what == 1) r->wr->w.snd = NULL;
        else r->wr->tl = 0;
    }
}

// -- the cart's files: its own folder and its written ones (SPEC.md 16.12) --
//
// Every request is moy_files' C over the kernel's volume
// (native/moy_store/moy_files.h), run on the VM's task through moy_wasm_on_vm
// while it waits on the call, and no Python is called: the volume's borrow
// resolves through the VM's mount table, and an internal-flash write disables
// the cache, which the session's PSRAM stack must not be running on. Each op
// runs inside the board's bus gate (moy_vol_gate_enter): on the T-Deck the
// card shares the panel's SPI host, and the gate drains the flush and holds
// the next one off for the op's length, as the Python store's session does.
// libmoy's binding holds every path to the manifest's "writable" entries and
// the size cap before it reaches these.
//
// THE KEEPER. In the browser the VFS is memory and OPFS the store of record:
// once the page's keeper is on (files_keep), a write is refused when the
// browser's last word on its room says it will not fit, and every write and
// erase is logged for the keeper to make durable (files_kept, drained by
// firmware/web_runner/carts_link.py's pump), in the order they were made.

enum { FOP_READ, FOP_WRITTEN, FOP_WRITE, FOP_ERASE, FOP_LIST };

typedef struct {
    int op;
    const char *path;            // the path, read's name, or list's prefix
    const uint8_t *data;         // write's
    uint32_t offset;             // the reads'
    uint32_t index;              // list's
    uint8_t *dst;                // the reads' and list's
    uint32_t len;                // write's length; the reads' and list's room
    int32_t r;
} wfiles_t;

static struct {
    int on;                      // the page's keeper is listening
    int known;                   // the browser has said how much room it has
    uint64_t usage, quota;
    char *log;                   // "<op><cart id>\0<key>\0" per entry
    size_t n, cap;
} g_keep;

static void keep_log(char op, const char *path)
{
    char key[3 * MOY_FILES_PATH + 1];
    const char *id;
    size_t pn = strlen(path), kn, idn, need;
    char *log;
    if (!g_keep.on || !WR || !WR->files) return;
    kn = moy_files_key((const uint8_t *)path, pn, key, sizeof(key));
    if (kn == 0) return;
    id = strrchr(WR->keep_dir, '/');
    id = id ? id + 1 : WR->keep_dir;
    idn = strlen(id);
    need = g_keep.n + 1 + idn + 1 + kn + 1;
    if (need > g_keep.cap) {
        size_t cap = g_keep.cap ? g_keep.cap : 256;
        while (cap < need) cap *= 2;
        if ((log = (char *)realloc(g_keep.log, cap)) == NULL) return;
        g_keep.log = log;
        g_keep.cap = cap;
    }
    log = g_keep.log + g_keep.n;
    *log++ = op;
    memcpy(log, id, idn + 1);
    memcpy(log + idn + 1, key, kn + 1);
    g_keep.n = need;
}

extern void moy_store_unwind(void);

static void files_now(void *arg)
{
    wfiles_t *q = (wfiles_t *)arg;
    moy_files_t *f = WR ? WR->files : NULL;
    nlr_buf_t nlr;
    if (f == NULL) return;                  // q->r stays the refusal
    int gated = moy_vol_gate_enter(WR->dir);
    if (nlr_push(&nlr) == 0) {
        size_t n = strlen(q->path);
        switch (q->op) {
            case FOP_READ:
                q->r = moy_files_read(f, q->path, q->offset, q->dst, q->len);
                break;
            case FOP_WRITTEN:
                q->r = moy_files_read_written(f, (const uint8_t *)q->path, n, q->offset,
                                              q->dst, q->len);
                break;
            case FOP_WRITE:
                if (g_keep.on && g_keep.known
                    && (g_keep.quota < g_keep.usage || g_keep.quota - g_keep.usage < q->len)) {
                    q->r = MOY_WASM_NO_ROOM;
                    break;
                }
                q->r = moy_files_write(f, (const uint8_t *)q->path, n, q->data, q->len);
                if (q->r == 0) keep_log('p', q->path);
                break;
            case FOP_ERASE:
                q->r = moy_files_erase(f, (const uint8_t *)q->path, n);
                if (q->r == 0) keep_log('d', q->path);
                break;
            default:
                q->r = moy_files_name(f, (const uint8_t *)q->path, n, q->index, q->dst, q->len);
                break;
        }
        nlr_pop();
    } else {
        moy_store_unwind();                 // a volume that raised: the refusal
    }
    moy_vol_gate_leave(gated);
}

static int32_t files_ask(wfiles_t *q)
{
    if (moy_wasm_on_vm(files_now, q) != 0) return q->op == FOP_WRITE ? MOY_WASM_FAILED : -1;
    return q->r;
}

static uint32_t hw_read(void *user, const char *name, uint32_t offset,
                        uint8_t *dst, uint32_t len)
{
    wfiles_t q = { FOP_READ, name, NULL, offset, 0, dst, len, 0 };
    (void)user;
    int32_t r = files_ask(&q);
    return r < 0 ? 0 : (uint32_t)r;
}

static int32_t hw_written(void *user, const char *path, uint32_t offset,
                          uint8_t *dst, uint32_t len)
{
    wfiles_t q = { FOP_WRITTEN, path, NULL, offset, 0, dst, len, -1 };
    (void)user;
    return files_ask(&q);
}

static int32_t hw_write(void *user, const char *path, const uint8_t *data, uint32_t len)
{
    wfiles_t q = { FOP_WRITE, path, data, 0, 0, NULL, len, MOY_WASM_FAILED };
    (void)user;
    return files_ask(&q);
}

static int32_t hw_erase(void *user, const char *path)
{
    wfiles_t q = { FOP_ERASE, path, NULL, 0, 0, NULL, 0, -1 };
    (void)user;
    return files_ask(&q);
}

static int32_t hw_list(void *user, const char *prefix, uint32_t index, uint8_t *dst,
                       uint32_t len)
{
    wfiles_t q = { FOP_LIST, prefix, NULL, 0, index, dst, len, -1 };
    (void)user;
    return files_ask(&q);
}

// The session's files opened and closed, on the VM's task, inside the gate:
// the open finishes what a power loss left.
static void files_open(const char *dir)
{
    int gated = moy_vol_gate_enter(dir);
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        WR->files = moy_files_open(dir);
        nlr_pop();
    } else {
        moy_store_unwind();
        WR->files = NULL;
    }
    moy_vol_gate_leave(gated);
    if (WR->files) moy_files_folder(dir, WR->keep_dir, sizeof(WR->keep_dir));
}

static void files_close(void)
{
    if (!WR || !WR->files) return;
    int gated = moy_vol_gate_enter(WR->dir);
    moy_files_close(WR->files);
    WR->files = NULL;
    moy_vol_gate_leave(gated);
}

// The cart's config, read on the engine's thread: the table is C's, copied in
// at run_begin and freed only after the session ends, so no VM is asked.
static const char *hw_cfg(void *user, const char *key)
{
    (void)user;
    return moycore_run_cfg(&RUN.c, key);
}

// A layer's pixels: PSRAM, the run's own, released by moy_wasm_close.
static moy_pixel *hw_layer_new(void *user, int w, int h)
{
    (void)user;
    return (moy_pixel *)wmem_calloc((size_t)w * (size_t)h, sizeof(moy_pixel));
}

static void hw_layer_free(void *user, moy_pixel *p)
{
    (void)user;
    wmem_free(p);
}

// -- the engine's callbacks, on its thread --

static int wo_runtime_up(void *user, char *err, size_t errlen)
{
    (void)user;
#if MOY_WASM
    uint32_t n = 0;
    moy_wasm_natives(&n);
    WR->natives = (NativeSymbol *)wmem_calloc(n, sizeof(NativeSymbol));
    if (!WR->natives) {
        snprintf(err, errlen, "out of memory: no PSRAM for the import table");
        return 1;
    }
    if (moy_wasm_register(WR->natives) != 0) {
        snprintf(err, errlen, "the import table did not register");
        return 1;
    }
    if (moy_app_wasm_natives != NULL) {
        uint32_t an = 0;
        const NativeSymbol *rows = moy_app_wasm_natives(&an);
        if (!moy_wasm_register_rows(APP_EXT, rows, an)) {
            snprintf(err, errlen, "the app extension's table did not register");
            return 1;
        }
        WR->app_natives = rows;
    }
#else
    // The page's adapters read the table where it is (moy_web_natives).
    (void)err;
    (void)errlen;
#endif
    return 0;
}

static int wo_loaded(void *user, moy_wasm_module module, char *err, size_t errlen)
{
    (void)user;
    moy_wasm_ext x = { NULL, NULL, 0 };
    uint32_t nx = app_ext(&x);
#if MOY_WASM
    const uint8_t *head = moycore_whead;
    size_t head_len = moycore_whead_len;
    if (moy_wasm_check_ext(module, head, head_len, moycore_wpages, &x, nx, err, errlen)) return 1;
#else
    const uint8_t *head = module->bytes;
    size_t head_len = module->size;
    if (moy_wasm_check_bytes_ext(module->bytes, module->size, moycore_wpages, &x, nx, err, errlen))
        return 1;
#endif
    // Each import from the extension against the run's grant, named when its
    // role is not one the cart's permissions name.
#if MOYCORE_APP
    return nx && moy_app_wasm_admit != NULL
               ? moy_app_wasm_admit(&WR->app, head, head_len, err, errlen) : 0;
#else
    (void)nx;
    (void)head;
    (void)head_len;
    return 0;
#endif
}

static int wo_bound(void *user, moy_wasm_env env, char *err, size_t errlen)
{
    (void)user;
#if MOY_WASM
    if (moy_wasm_open(&WR->w, &RUN.c.con, env) != 0) {
        snprintf(err, errlen, "a hook is missing");
        return 1;
    }
#else
    // The binding the page's adapters call the table with.
    (void)err;
    (void)errlen;
    moy_wasm_bind(&WR->w, &RUN.c.con);
    env->binding = &WR->w;
#endif
    return 0;
}

#if MOY_WASM
static uint32_t no_ring(const uint8_t *pcm, uint32_t n)
{
    (void)pcm;
    (void)n;
    return 0;
}

// The cart's timed lanes, once _init has returned (moy_wasm_session.h): the
// audio lane fills `snd`'s ring, which a struck lane mutes.
static int lanes_start(char *err, size_t errlen)
{
    moy_wasm_tlane_sink_t sink = {no_ring, NULL};
#if MOY_AUDIO_SND
    if (WR->w.snd) {
        sink.write = moy_audio_snd;
        sink.mute = moy_audio_snd_mute;
    }
#endif
    others_lose(2);
    WR->tl = 1;
    return moy_wasm_tlanes_start(WR->w.env, &sink, err, errlen) < 0;
}
#endif

static int wo_call(void *user, int what, float dt, char *err, size_t errlen)
{
    (void)user;
#if MOY_WASM
    if (what == WCALL_INIT) {
        if (moy_wasm_init(&WR->w, err, errlen) != 0) return 1;
        return lanes_start(err, errlen);
    }
    if (what == WCALL_UPDATE) return moy_wasm_update(&WR->w, dt, err, errlen);
    return moy_wasm_draw(&WR->w, err, errlen);
#else
    // The page calls the export; the binding brackets the call. A trap the
    // binding raised names itself; anything else the cart threw (an
    // unreachable, an access outside its memory) is the engine's message.
    int hook = what == WCALL_INIT ? MOY_WASM_INIT
             : what == WCALL_UPDATE ? MOY_WASM_UPDATE : MOY_WASM_DRAW;
    moy_wasm_begin(&WR->w, hook);
    err[0] = 0;
    int threw = moy_wasm_session_export(hook, dt, err, errlen);
    if (!moy_wasm_end(&WR->w, threw)) return 0;
    const char *trap = moy_wasm_trapped(&WR->w);
    if (trap) snprintf(err, errlen, "%s", trap);
    else if (!err[0]) snprintf(err, errlen, "the cart trapped");
    return 1;
#endif
}

static void wo_unbound(void *user)
{
    (void)user;
#if MOY_WASM
    if (WR->tl) moy_wasm_tlanes_stop();
    // The runtime outlives this session where another is open: the import
    // table's registration from this run's storage goes now.
    if (WR->natives) wasm_runtime_unregister_natives(MOY_WASM_MODULE, WR->natives);
    if (WR->app_natives) wasm_runtime_unregister_natives(APP_EXT, WR->app_natives);
#endif
    moy_wasm_close(&WR->w);
}

#if MOY_AUDIO_SND
// The cart's samples, from the engine's thread into the speaker's mixer.
static uint32_t wo_snd(void *user, const uint8_t *pcm, uint32_t n)
{
    (void)user;
    return moy_audio_snd(pcm, n);
}
#endif

static const moy_wasm_ops WASM_OPS = {
    NULL, wo_runtime_up, wo_loaded, wo_bound, wo_call, wo_unbound,
};

#if MOY_WASM
// The cart's par items on the session's lanes, from the engine's thread.
static int wo_lane_go(void *user, int lane, void (*work)(void *), void *job)
{
    (void)user;
    return moy_wasm_session_lane_go(lane, work, job);
}

static void wo_lane_wait(void *user, int lane)
{
    (void)user;
    moy_wasm_session_lane_wait(lane);
}

// The frame hand-off (libmoy/moy_wasm.h's `frame`): while the board says it
// can show a frame from the cart's memory (take_frames), every blit of a
// layout it takes leaves its frame there, owed, and the canvas is written only
// when something needs it -- a verb over it, a settle, or the cart's next
// hook. On the engine's thread, so it only answers. TAKE_565 and TAKE_PALETTE
// are the two layouts: blit565's words, and blit's indices with their colours.
#define TAKE_565 MOYCORE_TAKE_565
#define TAKE_PALETTE MOYCORE_TAKE_PALETTE

static int wo_frame(void *user, const uint8_t *pixels, const moy_pixel *lut)
{
    (void)user;
    (void)pixels;
    return WR->take & (lut ? TAKE_PALETTE : TAKE_565);
}
#endif // MOY_WASM

// A trap ends the run, and the frame it interrupted is never presented: the
// canvas is cleared before the console paints its report over it, and the
// last frame shown is not shown again over the report.
static void wasm_trapped(void)
{
    WR->dead = 1;
    WR->w.kept = NULL;
    moy_reset_state(&RUN.c.canvas);
    moy_cls(&RUN.c.canvas, 0);
}

int moycore_wasm_begin(const char *path, const char *sha, const char *dir,
                       const char *writable, size_t writable_len,
                       int swapped, int allow_unsigned, int interp, uint32_t ring,
                       const struct moy_app_run *app, char *err, size_t errlen)
{
    if (WR) {
        wrun_free(WR);
        WR = NULL;
    }
    moy_wasm_session_use(NULL);         // another run's session is not this one's
    if ((WR = (wrun_t *)wmem_calloc(1, sizeof(wrun_t))) == NULL) {
        snprintf(err, errlen, "out of memory: no PSRAM for the cart's run state");
        return 1;
    }
    WR->w.read = hw_read;
    WR->w.wire_swapped = swapped;
#if MOYCORE_APP
    if (app) WR->app = *app;
#endif
    moy_proc_bind(moycore_RUN.proc, &WR->w, app);
    // The manifest's "writable" entries, NUL-separated as Python joined them,
    // and two NULs to end libmoy's list.
    if (writable_len) {
        WR->writable = (char *)wmem_calloc(writable_len + 2, 1);
        if (!WR->writable) {
            snprintf(err, errlen, "out of memory: no PSRAM for the cart's run state");
            return 1;
        }
        memcpy(WR->writable, writable, writable_len);
        WR->w.writable = WR->writable;
    }
    WR->w.written = hw_written;
    WR->w.write = hw_write;
    WR->w.erase = hw_erase;
    WR->w.list = hw_list;
#if MOY_WASM
    WR->w.frame = wo_frame;
    WR->w.lanes = moy_wasm_session_lanes();
    WR->w.lane_go = wo_lane_go;
    WR->w.lane_wait = wo_lane_wait;
    WR->w.lane_stack = MOY_WASM_EXEC_STACK;
#endif
#if MOY_AUDIO_SND
    // The queue is as deep as the manifest's "audio_ring" asks (moy_audio_snd.h),
    // a depth outside the range being the default; a deep one the RINGS row
    // refuses leaves `snd` to the binding's clock, silent.
    if (ring < MOY_AUDIO_SND_MIN || ring > MOY_AUDIO_SND_MAX) ring = 0;
    if (moy_audio_snd_open(ring) > 0) {
        others_lose(1);
        WR->w.snd = wo_snd;
    }
#else
    (void)ring;
#endif
    snprintf(WR->dir, sizeof(WR->dir), "%s", dir);
    files_open(WR->dir);
    RUN.c.con.host.cfg = hw_cfg;
    RUN.c.con.host.layer_new = hw_layer_new;
    RUN.c.con.host.layer_free = hw_layer_free;
    // The linear memory the manifest declares, which the engine reads the
    // module into so the memory can take the block back; a declaration past
    // any board's PSRAM holds nothing and is refused at the check.
    uint32_t memory = moycore_wpages <= 1024 ? moycore_wpages * 65536u : 0;
    if (moy_wasm_session_open(path, sha, memory, allow_unsigned, interp, &WASM_OPS,
                              err, errlen) != 0) {
        files_close();
        return 1;
    }
    WR->sess = moy_wasm_session_current();
    RUN.wasm = 1;
    moycore_run_tick_begin();
    if (moy_wasm_session_call(WCALL_INIT, 0.0f, err, errlen) != 0) {
        wasm_trapped();
        return 1;
    }
    return 0;
}

int moycore_wasm_tick_c(float dt, int draw, char *err, size_t errlen)
{
    uint32_t t0 = (uint32_t)mp_hal_ticks_us(), t1;
    if (!WR || WR->dead) {
        snprintf(err, errlen, "the cart is not running");
        return 1;
    }
    moy_wasm_session_use(WR->sess);
    if (moy_wasm_session_call(WCALL_UPDATE, dt, err, errlen) != 0) {
        wasm_trapped();
        return 1;
    }
    t1 = (uint32_t)mp_hal_ticks_us();
    // quit() ends the cart where it stands (moy-spec SPEC.md 16.4).
    if (draw && !WR->w.quitting
        && moy_wasm_session_call(WCALL_DRAW, 0.0f, err, errlen) != 0) {
        wasm_trapped();
        return 1;
    }
    moycore_run_set_split(t1 - t0, draw ? (uint32_t)mp_hal_ticks_us() - t1 : 0);
    return 0;
}

void moycore_wasm_end(void)
{
    if (RUN.wasm && WR) {
        moy_wasm_session_use(WR->sess);
        moy_wasm_session_close();
    }
    RUN.wasm = 0;
#if MOY_AUDIO_SND
    if (WR && WR->w.snd) moy_audio_snd_close();    // the stream is this run's
#endif
    if (WR) {
        wrun_free(WR);
        WR = NULL;
    }
}
#endif // MOYCORE_WASM

// wasm_open(module_path, wasm_head, pages, wasm_sha, cart_dir, wire_swapped,
//           allow_unsigned=False, interp=False, writable=None, ring=0, app=None)
//           -> None, or the refusal or trap as text
//
// After run_begin(..., vm=False): load the compiled module at `module_path`
// on the engine, check it against the canonical .wasm's head (`wasm_head`,
// enough of main.wasm to reach its memory section) and the manifest's `pages`
// before its memory exists, refuse it unless its key names `wasm_sha`, bind it
// to this console and run _init. `cart_dir` is the only folder `read` sees;
// `wire_swapped` says the canvas stores its words byte-swapped, which a
// palette blit must match. `allow_unsigned` is the cart's consent
// (app_perms.run_admits_unsigned): true lets an AOT module with no signature load
// (moy_wasm_session.h). `interp` is true when `module_path` IS the cart's own
// main.wasm, run on the interpreter tier (native/moy_wasm/README.md, "A
// cart survives its firmware", 2026-09-30): no key, no signature, no
// admission, and `wasm_sha` is ignored. `writable` is the manifest's
// "writable" entries joined by NUL characters (moy-spec SPEC.md 16.12; the
// cart's files are moy_files', above). `ring` is the manifest's "audio_ring",
// the depth of `snd`'s queue in frames (0: moy-spec's 2,048). `app` is the
// app's run (moycore_app_run), which the run holds for its imports from the
// app extension and its load's grant check, or None for none. A refusal or a
// trap closes nothing -- close() does.
static mp_obj_t mod_wasm_open(size_t n_args, const mp_obj_t *a)
{
    if (!RUN.open || RUN.L || RUN.wasm)
        mp_raise_msg(&mp_type_RuntimeError,
                     MP_ERROR_TEXT("moycore: wasm_open wants a run begun with vm=False"));
#if MOYCORE_WASM
    char err[192];
    size_t hlen = 0, wlen = 0;
    const char *writable = NULL;
    if (n_args > 8 && a[8] != mp_const_none) writable = mp_obj_str_get_data(a[8], &wlen);
    mp_int_t ring = n_args > 9 && a[9] != mp_const_none ? mp_obj_get_int(a[9]) : 0;
    moycore_whead = (const uint8_t *)moycore_buf_r(a[1], &hlen);
    moycore_whead_len = hlen;
    moycore_wpages = (uint32_t)mp_obj_get_int(a[2]);
    const char *sha = a[3] == mp_const_none ? NULL : mp_obj_str_get_str(a[3]);
    int rc = moycore_wasm_begin(mp_obj_str_get_str(a[0]), sha, mp_obj_str_get_str(a[4]),
                                writable, wlen,
                                mp_obj_is_true(a[5]), n_args > 6 && mp_obj_is_true(a[6]),
                                n_args > 7 && mp_obj_is_true(a[7]),
                                ring > 0 ? (uint32_t)ring : 0u,
                                moycore_app_run(n_args > 10 ? a[10] : mp_const_none),
                                err, sizeof(err));
    moycore_whead = NULL;
    moycore_whead_len = 0;
    if (rc) return mp_obj_new_str(err, strlen(err));
    return mp_const_none;
#else
    (void)n_args;
    (void)a;
    mp_raise_msg(&mp_type_RuntimeError,
                 MP_ERROR_TEXT("moycore: this build has no wasm engine"));
#endif
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_wasm_open_obj, 6, 11, mod_wasm_open);

// files_keep(on): the page's keeper is listening, or not (the browser's,
// firmware/web_runner/carts_link.py); turning it off drops the log.
static mp_obj_t mod_files_keep(mp_obj_t on)
{
#if MOYCORE_WASM
    g_keep.on = mp_obj_is_true(on);
    if (!g_keep.on) {
        free(g_keep.log);
        g_keep.log = NULL;
        g_keep.n = g_keep.cap = 0;
        g_keep.known = 0;
    }
#else
    (void)on;
#endif
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_1(moycore_files_keep_obj, mod_files_keep);

// files_room(usage, quota): the browser's last word on its store's room.
static mp_obj_t mod_files_room(mp_obj_t usage, mp_obj_t quota)
{
#if MOYCORE_WASM
    g_keep.usage = (uint64_t)mp_obj_get_float(usage);
    g_keep.quota = (uint64_t)mp_obj_get_float(quota);
    g_keep.known = 1;
#else
    (void)usage;
    (void)quota;
#endif
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_2(moycore_files_room_obj, mod_files_room);

// files_kept() -> [(op, cart, key), ...]: the writes ("wput") and erases
// ("wdel") made since the last call, oldest first, for the keeper to make
// durable.
static mp_obj_t mod_files_kept(void)
{
    mp_obj_t out = mp_obj_new_list(0, NULL);
#if MOYCORE_WASM
    size_t i = 0;
    while (i < g_keep.n) {
        const char *id = g_keep.log + i + 1;
        const char *key = id + strlen(id) + 1;
        mp_obj_t t[3] = {
            mp_obj_new_str(g_keep.log[i] == 'p' ? "wput" : "wdel", 4),
            mp_obj_new_str(id, strlen(id)),
            mp_obj_new_str(key, strlen(key)),
        };
        mp_obj_list_append(out, mp_obj_new_tuple(3, t));
        i = (size_t)(key + strlen(key) + 1 - g_keep.log);
    }
    g_keep.n = 0;
#endif
    return out;
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_files_kept_obj, mod_files_kept);

// wasm_quit() -> whether the cart called quit(): it ended itself, and the
// run must not call it again.
static mp_obj_t mod_wasm_quit(void)
{
#if MOYCORE_WASM
    return mp_obj_new_bool(RUN.wasm && WR && WR->w.quitting);
#else
    return mp_const_false;
#endif
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_wasm_quit_obj, mod_wasm_quit);

// Whether the run's module is open: it loaded, and its _init was called. A
// refusal before that point leaves the run free for another open (a module
// this console cannot use, retried on the interpreter); an error after it is
// the cart's own, a trap in its code, and nothing retries it.
int moycore_wasm_ran(void)
{
    return RUN.wasm;
}

// wasm_ran() -> moycore_wasm_ran(), for the binding's open.
static mp_obj_t mod_wasm_ran(void)
{
    return mp_obj_new_bool(moycore_wasm_ran());
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_wasm_ran_obj, mod_wasm_ran);

// wasm_table() -> the names in this console's import table (libmoy's
// moy_wasm_natives), every function a compiled cart may import from "moy":
// what the Player holds a module's imports to before it loads one
// (moycore_glue.missing_imports). () in a build with no wasm engine.
static mp_obj_t mod_wasm_table(void)
{
#if MOYCORE_WASM
    uint32_t n = 0;
    const NativeSymbol *rows = moy_wasm_natives(&n);
    mp_obj_tuple_t *t = MP_OBJ_TO_PTR(mp_obj_new_tuple(n, NULL));
    for (uint32_t i = 0; i < n; i++)
        t->items[i] = mp_obj_new_str(rows[i].symbol, strlen(rows[i].symbol));
    return MP_OBJ_FROM_PTR(t);
#else
    return mp_const_empty_tuple;
#endif
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_wasm_table_obj, mod_wasm_table);

// take_frames(on, palette=True) -- whether the current compiled run's blits
// leave their frames in its memory for the board to show
// (frame/frame_presented) instead of writing the canvas: blit565's when
// `on`, and blit's too unless `palette` is false, for a board that cannot
// show a palette frame. Each run's own, off until it is asked.
static mp_obj_t mod_take_frames(size_t n_args, const mp_obj_t *a)
{
#if MOY_WASM
    int take = 0;
    if (mp_obj_is_true(a[0])) {
        take = TAKE_565;
        if (n_args < 2 || mp_obj_is_true(a[1])) take |= TAKE_PALETTE;
    }
    if (WR) WR->take = take;
#else
    (void)n_args;
    (void)a;
#endif
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_take_frames_obj, 1, 2, mod_take_frames);

// frame(lut_out) -> the owed frame as a memoryview into the cart's memory, or
// None. W x H bytes are blit's indices, whose 256 wire colours are copied into
// `lut_out` (512 bytes); 2 x W x H bytes are blit565's little-endian words.
// Valid until the cart's next hook, which the board fences.
//
// The view is ONE object, re-aimed at every owed frame: it is asked for on
// every frame the cart draws, and it outlives nothing -- a frame is shown or
// settled before the cart's next hook. It lives outside the heap, like the
// memory it points into.
#if MOY_WASM
static mp_obj_array_t g_frame_view;
#endif
static mp_obj_t mod_frame(mp_obj_t lut_out)
{
#if MOY_WASM
    const moy_pixel *lut = NULL;
    const uint8_t *px = (RUN.wasm && WR) ? moy_wasm_frame(&WR->w, &lut) : NULL;
    if (!px) return mp_const_none;
    size_t n = (size_t)RUN.c.canvas.w * (size_t)RUN.c.canvas.h;
    if (lut) {
        size_t len = 0;
        uint8_t *out = (uint8_t *)moycore_buf_w(lut_out, &len);
        if (len < 256 * sizeof(moy_pixel))
            mp_raise_ValueError(MP_ERROR_TEXT("frame: palette buffer too small"));
        memcpy(out, lut, 256 * sizeof(moy_pixel));
    } else {
        n *= 2;
    }
    mp_obj_array_t *v = &g_frame_view;
    v->base.type = &mp_type_memoryview;
    v->typecode = 'B';
    v->free = 0;                     // a memoryview's offset into its buffer
    v->len = n;
    v->items = (void *)px;
    return MP_OBJ_FROM_PTR(v);
#else
    (void)lut_out;
    return mp_const_none;
#endif
}
MP_DEFINE_CONST_FUN_OBJ_1(moycore_frame_obj, mod_frame);

// frame_kept() -> whether the binding still holds a frame the canvas lacks, in
// the copy frame_presented named: the last frame shown, which no blit has
// replaced and no verb has written into the canvas since.
static mp_obj_t mod_frame_kept(void)
{
#if MOY_WASM
    return mp_obj_new_bool(RUN.wasm && WR && WR->w.kept != NULL);
#else
    return mp_const_false;
#endif
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_frame_kept_obj, mod_frame_kept);

// frame_settle() -- write the frame the canvas lacks into it now, as the blit
// would have: the owed one, or the last one shown from the copy
// frame_presented named. For anything about to draw on the canvas, or read
// it, while the frame is still the cart's or the board's. A kept frame is
// handed back as owed so the binding's own settle writes it, in the layout
// and with the colours of the blit that made it.
static mp_obj_t mod_frame_settle(void)
{
    moycore_wasm_settle();
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_0(moycore_frame_settle_obj, mod_frame_settle);

// The canvas a compiled app's ui rows draw on (native/moy_ui/moy_ui_wasm.h):
// the run's draw target, an owed or kept frame on the screen written in
// first, as libmoy's own verbs settle it before they draw. NULL with no
// compiled run, or inside one of par's items, which reach no import.
void *moycore_ui_target(void);
void *moycore_ui_target(void)
{
#if MOYCORE_WASM
    if (!RUN.wasm || !WR || WR->w.items) return NULL;
    moy_wasm *w = &WR->w;
#if MOY_WASM
    if (w->target == w->screen && (w->owed || w->kept)) {
        if (!w->owed) {
            w->owed = w->kept;
            w->kept = NULL;
        }
        moy_wasm_settle(w);
    }
#endif
    return w->target;
#else
    return NULL;
#endif
}

// frame_presented(kept=None, off=0) -- the board has shown the owed frame.
// `kept[off:]` is its copy of the frame, byte for byte, alive until the next
// blit; the canvas is written from it if the cart draws on the screen before
// then. None says the board wrote the frame into the canvas itself.
static mp_obj_t mod_frame_presented(size_t n_args, const mp_obj_t *a)
{
#if MOY_WASM
    const uint8_t *kept = NULL;
    if (n_args > 0 && a[0] != mp_const_none) {
        size_t len = 0;
        size_t off = n_args > 1 ? (size_t)mp_obj_get_int(a[1]) : 0;
        const uint8_t *b = (const uint8_t *)moycore_buf_r(a[0], &len);
        size_t need = (size_t)RUN.c.canvas.w * (size_t)RUN.c.canvas.h;
        if (WR && WR->w.frame_565) need *= 2;
        if (off > len || len - off < need)
            mp_raise_ValueError(MP_ERROR_TEXT("frame_presented: copy too small"));
        kept = b + off;
    }
    if (RUN.wasm && WR) moy_wasm_presented(&WR->w, kept);
#else
    (void)n_args;
    (void)a;
#endif
    return mp_const_none;
}
MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(moycore_frame_presented_obj, 0, 2,
                                    mod_frame_presented);

// The same hand-off for the kernel's front (moycore_lua.h).
void moycore_wasm_take(int take)
{
#if MOY_WASM
    if (WR) WR->take = take & (TAKE_565 | TAKE_PALETTE);
#else
    (void)take;
#endif
}

const uint8_t *moycore_wasm_owed(const uint16_t **lut, bool *kept)
{
    *lut = NULL;
    *kept = false;
#if MOY_WASM
    if (!RUN.wasm || !WR) return NULL;
    *kept = WR->w.kept != NULL;
    const moy_pixel *l = NULL;
    const uint8_t *px = moy_wasm_frame(&WR->w, &l);
    *lut = (const uint16_t *)l;
    return px;
#else
    return NULL;
#endif
}

void moycore_wasm_presented(const uint8_t *kept)
{
#if MOY_WASM
    if (RUN.wasm && WR) moy_wasm_presented(&WR->w, kept);
#else
    (void)kept;
#endif
}

void moycore_wasm_settle(void)
{
#if MOY_WASM
    if (RUN.wasm && WR) {
        if (!WR->w.owed && WR->w.kept) {
            WR->w.owed = WR->w.kept;
            WR->w.kept = NULL;
        }
        moy_wasm_settle(&WR->w);
    }
#endif
}

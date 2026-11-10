// moycore_svc: the Lua tier's services. moycore_svc.h has the contract.

// The host's clock_gettime under a strict C standard.
#if defined(__has_include) && !defined(_POSIX_C_SOURCE)
#if !__has_include("esp_heap_caps.h")
#define _POSIX_C_SOURCE 200809L
#endif
#endif

#include "moycore_svc.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "lauxlib.h"
#include "lua.h"
#include "lualib.h"
#include "moy_evq.h"
#include "moy_json.h"
#include "moy_proc_exit.h"
#include "moy_super.h"
#include "moy_vmq.h"

// The console calls a service's `moybyte` table holds, generated from
// native/os_calls.json (none in an image without the app ABI).
#if defined(__has_include) && __has_include("moy_app_lua_rows.h")
#include "moy_app_lua_rows.h"
#else
#define MOY_APP_LUA_CALLS(X)
#endif

#if defined(__has_include)
#if __has_include("esp_heap_caps.h")
#define SVC_BOARD 1
#endif
#endif

#ifdef SVC_BOARD
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/idf_additions.h"
#include "sdkconfig.h"
#define SVC_TASKS 1
#if CONFIG_IDF_TARGET_ESP32P4
#define SVC_CORE 0
#else
// The S3s: core 1, the VM's (core 0 has the flush feed and fold, input and
// the radios), and the task's stack in PSRAM, so a service holds no internal
// SRAM past its task's control block (the shares' internal cap, moy_play.h).
// The task never touches flash itself: its file rows hop to the VM's task.
#define SVC_CORE 1
#define SVC_STACK_PSRAM 1
#endif
#endif

// The OS log (native/moy_kernel/moy_log.h), where the image has it.
void moy_log_write(int level, uint32_t pid, const char *text, size_t n) __attribute__((weak));
enum { LOG_ERR = 3, LOG_WARNING = 4, LOG_INFO = 6 };

// The prefs names' JSON (native/moy_app/moy_app_lua.h), where the image has
// the app ABI: a message's arguments, the console's names and its answers.
int moy_app_lua_json_push(lua_State *L, const char *text, size_t n) __attribute__((weak));
const char *moy_app_lua_json_dump(lua_State *L, int idx, size_t *n) __attribute__((weak));
// The crypto rows (moy_app_lua.h), which need no grant.
int moy_app_lua_open_crypto(lua_State *L) __attribute__((weak));

// The role names of its app's run (moy_app_lua.h), where the image has the
// app ABI.
#if defined(__has_include)
#if __has_include("moy_app_run.h")
#include "moy_app_run.h"
#define SVC_APP 1
int moy_app_lua_open_hop(lua_State *L, const struct moy_app_run *run,
                         int (*hop)(lua_State *L, void (*fn)(void *), void *arg, int here))
    __attribute__((weak));
#endif
#endif

#define HOOK_COUNT 1000         // instructions between the hook's looks at the clock

enum { CALL_OK, CALL_ERR, CALL_ABORT, CALL_EXIT, CALL_WAIT };
// What follows a call's end.
enum { THEN_CHUNK, THEN_START, THEN_STOP, THEN_CB };
// A console call's passage: posted on the VM's queue, answered by the console.
enum { REQ_NONE, REQ_POSTED, REQ_ANSWERED };

typedef struct {
    uint32_t id;                // 0: a free timer
    uint32_t due;               // ms
    uint32_t every;             // 0: once
    int ref;                    // the callback, in the registry
} svc_timer_t;

typedef struct {
    uint32_t id;                // 0: a free wake
    int ref;                    // the handler, in the registry
    char name[MOY_EVQ_NAME];
} svc_on_t;

typedef struct {
    lua_State *L;
    char *src;                  // its scripts, until its start loads them (pack_chunks)
    size_t n;
    char *env;                  // what the console hands it, until its start reads it
    size_t envn;
    uint32_t pid;
    uint16_t gen;
    volatile uint8_t state;
    volatile uint8_t stop_req;
    volatile uint8_t done;      // its task is gone (or it never had one)
    volatile int32_t exit;
    volatile uint32_t heap;
    volatile uint32_t cpu_us;
    uint32_t now;               // the step's clock, ms
    uint32_t next_id;
    svc_timer_t tm[MOYCORE_SVC_TIMERS];
    svc_on_t on[MOYCORE_SVC_ONS];
    uint8_t own_q;              // its event queue is its own, opened at its start
    uint8_t orphan;             // its VM stopped before its task was done
    // the call under way: its coroutine (anchored at co_ref), NULL for none
    lua_State *co;
    int co_ref;
    uint8_t then;
    int64_t seg;                // when its current run began
    int64_t t0;                 // when it began, less its waits
    int64_t spent;              // what it has run, its waits apart
    uint32_t budget;
    uint8_t aborted;
    uint8_t exiting;
    int32_t exit_req;
    // the message it delivers: taken from its event queue, handed to each of
    // its wakes on the name in turn (msg_next the next to look at)
    char *msg;                  // its arguments, a JSON array; NULL: none
    size_t msg_n;
    uint8_t msg_next;
    moy_evq_ev_t ev;            // its sender and its name
    // its console call, on the VM's queue (moy_vmq.h)
    uint8_t req;                // REQ_*
    uint32_t ticket;
    char req_name[MOY_VMQ_NAME];
    char *ans;                  // the answer, NULL: refused
    size_t ans_n;
    char id[32];
    char why[MOYCORE_SVC_WHY];
#ifdef SVC_APP
    moy_app_run_t run;          // its app's run, g 0 for none
#endif
#ifdef SVC_TASKS
    TaskHandle_t task;
#endif
} svc_t;

static svc_t *s_row[MOYCORE_SVCS];
static uint16_t s_gen;

// A row is put in or taken out of the table under the kernel's lock
// (native/moy_kernel/moy_lock.h; weak, for a build without it, whose one task
// needs none), so moycore_svc_of may walk it from any task; every other walk
// is the VM's task's, which alone writes it.
void moy_lock_take(void) __attribute__((weak));
void moy_lock_give(void) __attribute__((weak));

static void row_put(uint32_t slot, svc_t *s) {
    if (moy_lock_take != NULL) {
        moy_lock_take();
    }
    s_row[slot] = s;
    if (moy_lock_give != NULL) {
        moy_lock_give();
    }
}

// -- the clock, the memory -------------------------------------------------------

static int64_t now_us(void) {
    #ifdef SVC_BOARD
    return esp_timer_get_time();
    #else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
    #endif
}

static void *mem_realloc(void *p, size_t n) {
    #ifdef SVC_BOARD
    return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM);
    #else
    return realloc(p, n);
    #endif
}

static void *mem_calloc(size_t n) {
    #ifdef SVC_BOARD
    return heap_caps_calloc(1, n, MALLOC_CAP_SPIRAM);
    #else
    return calloc(1, n);
    #endif
}

static char *mem_copy(const char *p, size_t n) {
    char *c = mem_realloc(NULL, n + 1u);
    if (c != NULL) {
        if (n) {
            memcpy(c, p, n);
        }
        c[n] = 0;
    }
    return c;
}

// The state's allocator: every byte it holds counted, HEAP_MAX the cap.
static void *svc_alloc(void *ud, void *ptr, size_t osize, size_t nsize) {
    svc_t *s = ud;
    if (ptr == NULL) {
        osize = 0;
    }
    if (nsize == 0) {
        free(ptr);
        s->heap -= (uint32_t)osize;
        return NULL;
    }
    if (nsize > osize && s->heap + (nsize - osize) > MOYCORE_SVC_HEAP_MAX) {
        return NULL;
    }
    void *p = mem_realloc(ptr, nsize);
    if (p != NULL) {
        s->heap += (uint32_t)nsize - (uint32_t)osize;
    }
    return p;
}

static svc_t *of_state(lua_State *L) {
    return *(svc_t **)lua_getextraspace(L);
}

// -- a call ------------------------------------------------------------------------

static void hook(lua_State *L, lua_Debug *ar) {
    (void)ar;
    svc_t *s = of_state(L);
    if (!s->aborted && !s->exiting && (uint32_t)(now_us() - s->t0) <= s->budget) {
        return;
    }
    s->aborted = s->aborted || !s->exiting;
    if (lua_isyieldable(L)) {
        lua_yield(L, 0);
        return;
    }
    luaL_error(L, "aborted: past its time");
}

// Run the call's coroutine on, `nargs` values on its stack's top: how it
// stopped. A call that waits on a console call keeps its coroutine.
static int resume(svc_t *s, int nargs) {
    lua_State *co = s->co;
    s->seg = now_us();
    s->t0 = s->seg - s->spent;
    int nres = 0;
    int rc = lua_resume(co, s->L, nargs, &nres);
    int64_t t = now_us();
    s->cpu_us += (uint32_t)(t - s->seg);
    s->spent = t - s->t0;
    int out;
    if (s->exiting) {
        out = CALL_EXIT;
    } else if (s->aborted) {
        out = CALL_ABORT;
    } else if (rc == LUA_YIELD && s->req == REQ_POSTED) {
        return CALL_WAIT;
    } else if (rc == LUA_OK) {
        out = CALL_OK;
    } else if (rc == LUA_YIELD) {
        snprintf(s->why, sizeof s->why, "a callback yielded outside a coroutine");
        out = CALL_ERR;
    } else {
        const char *m = lua_tostring(co, -1);
        snprintf(s->why, sizeof s->why, "%s", m != NULL ? m : "an error object");
        out = CALL_ERR;
    }
    luaL_unref(s->L, LUA_REGISTRYINDEX, s->co_ref);
    s->co = NULL;
    lua_settop(s->L, 0);
    return out;
}

// The function on top of the main state's stack and its `nargs` arguments,
// called on a fresh coroutine within `budget` microseconds; `then` is what
// follows its end.
static int call(svc_t *s, uint32_t budget, int nargs, uint8_t then) {
    lua_State *L = s->L;
    lua_State *co = lua_newthread(L);
    s->co_ref = luaL_ref(L, LUA_REGISTRYINDEX);
    lua_xmove(L, co, nargs + 1);
    lua_sethook(co, hook, LUA_MASKCOUNT, HOOK_COUNT);
    s->co = co;
    s->aborted = 0;
    s->budget = budget;
    s->spent = 0;
    s->then = then;
    return resume(s, nargs);
}

// -- the names -----------------------------------------------------------------------

static svc_timer_t *timer_of(svc_t *s, uint32_t id) {
    for (uint32_t i = 0; i < MOYCORE_SVC_TIMERS; i++) {
        if (s->tm[i].id == id && id != 0) {
            return &s->tm[i];
        }
    }
    return NULL;
}

static svc_on_t *on_of(svc_t *s, uint32_t id) {
    for (uint32_t i = 0; i < MOYCORE_SVC_ONS; i++) {
        if (s->on[i].id == id && id != 0) {
            return &s->on[i];
        }
    }
    return NULL;
}

static void timer_end(svc_t *s, svc_timer_t *t) {
    if (s->L != NULL) {
        luaL_unref(s->L, LUA_REGISTRYINDEX, t->ref);
    }
    t->id = 0;
}

static void on_end(svc_t *s, svc_on_t *o) {
    if (s->L != NULL) {
        luaL_unref(s->L, LUA_REGISTRYINDEX, o->ref);
    }
    if (o->id != 0) {
        moy_evq_listen(s->pid, o->name, false);
    }
    o->id = 0;
}

static uint32_t new_id(svc_t *s) {
    if (++s->next_id == 0) {
        s->next_id = 1;
    }
    return s->next_id;
}

static int timer_add(lua_State *L, int every) {
    svc_t *s = of_state(L);
    lua_Integer ms = luaL_checkinteger(L, 1);
    luaL_checktype(L, 2, LUA_TFUNCTION);
    if (ms < 1) {
        ms = 1;
    }
    svc_timer_t *t = timer_of(s, 0);
    for (uint32_t i = 0; t == NULL && i < MOYCORE_SVC_TIMERS; i++) {
        if (s->tm[i].id == 0) {
            t = &s->tm[i];
        }
    }
    if (t == NULL) {
        return luaL_error(L, "every timer of the service is taken");
    }
    lua_pushvalue(L, 2);
    t->ref = luaL_ref(L, LUA_REGISTRYINDEX);
    t->id = new_id(s);
    t->due = s->now + (uint32_t)ms;
    t->every = every ? (uint32_t)ms : 0u;
    lua_pushinteger(L, (lua_Integer)t->id);
    return 1;
}

static int l_every(lua_State *L) {
    return timer_add(L, 1);
}

static int l_after(lua_State *L) {
    return timer_add(L, 0);
}

static int l_on(lua_State *L) {
    svc_t *s = of_state(L);
    size_t n;
    const char *name = luaL_checklstring(L, 1, &n);
    luaL_checktype(L, 2, LUA_TFUNCTION);
    if (n == 0 || n >= MOY_EVQ_NAME) {
        return luaL_error(L, "no message %s", name);
    }
    svc_on_t *o = NULL;
    for (uint32_t i = 0; o == NULL && i < MOYCORE_SVC_ONS; i++) {
        if (s->on[i].id == 0) {
            o = &s->on[i];
        }
    }
    if (o == NULL) {
        return luaL_error(L, "every message wake of the service is taken");
    }
    if (moy_evq_listen(s->pid, name, true) != MOY_EVQ_OK) {
        return luaL_error(L, "the service listens on too many names");
    }
    lua_pushvalue(L, 2);
    o->ref = luaL_ref(L, LUA_REGISTRYINDEX);
    memcpy(o->name, name, n + 1u);
    o->id = new_id(s);
    lua_pushinteger(L, (lua_Integer)o->id);
    return 1;
}

static int l_cancel(lua_State *L) {
    svc_t *s = of_state(L);
    uint32_t id = (uint32_t)luaL_checkinteger(L, 1);
    svc_timer_t *t = timer_of(s, id);
    if (t != NULL) {
        timer_end(s, t);
    }
    svc_on_t *o = on_of(s, id);
    if (o != NULL) {
        on_end(s, o);
    }
    return 0;
}

static int l_log(lua_State *L) {
    lua_Integer level = luaL_checkinteger(L, 1);
    size_t n;
    const char *text = luaL_checklstring(L, 2, &n);
    if (moy_log_write != NULL) {
        moy_log_write(level < 0 ? 0 : level > 7 ? 7 : (int)level, of_state(L)->pid, text, n);
    }
    return 0;
}

static int l_print(lua_State *L) {
    int n = lua_gettop(L);
    luaL_Buffer b;
    luaL_buffinit(L, &b);
    for (int i = 1; i <= n; i++) {
        if (i > 1) {
            luaL_addchar(&b, '\t');
        }
        luaL_tolstring(L, i, NULL);
        luaL_addvalue(&b);
    }
    luaL_pushresult(&b);
    size_t len;
    const char *text = lua_tolstring(L, -1, &len);
    if (moy_log_write != NULL) {
        moy_log_write(LOG_INFO, of_state(L)->pid, text, len);
    }
    return 0;
}

static int l_exit(lua_State *L) {
    svc_t *s = of_state(L);
    s->exit_req = (int32_t)luaL_optinteger(L, 1, 0);
    s->exiting = 1;
    if (lua_isyieldable(L)) {
        return lua_yield(L, 0);
    }
    return luaL_error(L, "exit");
}

static int l_time(lua_State *L) {
    lua_pushinteger(L, (lua_Integer)time(NULL));
    return 1;
}

static int l_clock(lua_State *L) {
    svc_t *s = of_state(L);
    lua_pushnumber(L, (lua_Number)(s->cpu_us + (uint32_t)(now_us() - s->seg)) / 1e6f);
    return 1;
}

// A console call answered: its answer's value, or nil and why.
static int console_k(lua_State *L, int status, lua_KContext ctx) {
    (void)status;
    (void)ctx;
    svc_t *s = of_state(L);
    lua_settop(L, 0);
    luaL_checkstack(L, 2, NULL);
    if (s->ans != NULL && moy_app_lua_json_push(L, s->ans, s->ans_n)) {
        free(s->ans);
        s->ans = NULL;
        return 1;
    }
    free(s->ans);
    s->ans = NULL;
    lua_pushnil(L);
    lua_pushfstring(L, "the console refused %s", s->req_name);
    return 2;
}

// A message's sender: the app id the OS stamped on it.
static void push_sender(lua_State *L, const svc_t *s) {
    lua_pushstring(L, s->ev.app);
}

// A console call (its name the upvalue): its arguments posted as a JSON array
// and the call's coroutine yielded until the VM's task answers.
static int l_console(lua_State *L) {
    svc_t *s = of_state(L);
    if (L != s->co || !lua_isyieldable(L)) {
        return luaL_error(L, "a console call outside the service's own call");
    }
    if (moy_app_lua_json_dump == NULL || moy_app_lua_json_push == NULL) {
        return luaL_error(L, "no console calls in this build");
    }
    int n = lua_gettop(L);
    luaL_checkstack(L, 2 * n + 4, NULL);
    lua_pushliteral(L, "[");
    for (int i = 1; i <= n; i++) {
        if (i > 1) {
            lua_pushliteral(L, ", ");
        }
        moy_app_lua_json_dump(L, i, NULL);
    }
    lua_pushliteral(L, "]");
    lua_concat(L, n > 0 ? 2 * n + 1 : 2);
    size_t len;
    const char *text = lua_tolstring(L, -1, &len);
    snprintf(s->req_name, sizeof s->req_name, "%s", lua_tostring(L, lua_upvalueindex(1)));
    int rc = moy_vmq_post(s->pid, s->req_name, text, len, &s->ticket);
    if (rc != MOY_VMQ_OK) {
        return luaL_error(L, rc == MOY_VMQ_FULL ? "the console's queue is full"
                                                : "no memory for a console call");
    }
    lua_settop(L, 0);
    s->req = REQ_POSTED;
    return lua_yieldk(L, 0, 0, console_k);
}

// `moybyte.send(app, name, ...)`: the kernel's send (moy_evq.h) on the
// service's own task, as a compiled program's moy_send is, so it needs no
// console and goes on through a VM stop: how many took it, or nil and why
// ("full": BACKPRESSURE refused it; "no queue").
static int l_send(lua_State *L) {
    svc_t *s = of_state(L);
    const char *app = luaL_checkstring(L, 1);
    const char *name = luaL_checkstring(L, 2);
    if (moy_app_lua_json_dump == NULL) {
        return luaL_error(L, "no messages in this build");
    }
    int n = lua_gettop(L);
    luaL_checkstack(L, 2 * n + 4, NULL);
    lua_pushliteral(L, "[");
    for (int i = 3; i <= n; i++) {
        if (i > 3) {
            lua_pushliteral(L, ", ");
        }
        moy_app_lua_json_dump(L, i, NULL);
    }
    lua_pushliteral(L, "]");
    lua_concat(L, n >= 3 ? 2 * (n - 2) + 1 : 2);
    size_t len;
    const char *text = lua_tolstring(L, -1, &len);
    int took = moy_evq_send(s->pid, app, name, text, len, NULL);
    if (took >= 0) {
        lua_pushinteger(L, took);
        return 1;
    }
    lua_pushnil(L);
    lua_pushstring(L, took == -MOY_EVQ_FULL ? "full" : "no queue");
    return 2;
}

// `moybyte.at`'s kernel half (ALARMS): the kernel's alarm at wall time `t`
// (moy_evq_at, under the service's `alarms`), on the service's own task:
// its id, or nil and why.
static int l_at(lua_State *L) {
    svc_t *s = of_state(L);
    int64_t whole;
    if (lua_isinteger(L, 1)) {
        whole = (int64_t)lua_tointeger(L, 1);
    } else {
        // a fraction rounds up: no earlier than `t` (Lua's numbers are floats)
        lua_Number t = luaL_checknumber(L, 1);
        whole = t >= 4.0e9f ? (int64_t)4e9 : t <= -4.0e9f ? (int64_t)-4e9 : (int64_t)t;
        if ((lua_Number)whole < t) {
            whole++;
        }
    }
    uint32_t id = 0;
    int rc = moy_evq_at(s->pid, whole, &id);
    if (rc == MOY_EVQ_OK) {
        lua_pushinteger(L, (lua_Integer)id);
        return 1;
    }
    lua_pushnil(L);
    lua_pushstring(L, rc == MOY_EVQ_DENIED ? "moybyte.at needs \"alarms\""
                      : rc == MOY_EVQ_FULL ? "every timer of the service is taken"
                                           : "no queue");
    return 2;
}

static int deliver_k(lua_State *L, int status, lua_KContext ctx) {
    (void)L;
    (void)status;
    (void)ctx;
    return 0;
}

// A message delivered: its handler (at 1) called with its sender's app id and
// the arguments its JSON array holds, each in its place, a null as nil.
static int l_deliver(lua_State *L) {
    svc_t *s = of_state(L);
    luaL_checkstack(L, 1, NULL);
    push_sender(L, s);
    int n = 1;
    if (s->msg != NULL && moy_app_lua_json_push != NULL) {
        const char *end = s->msg + s->msg_n;
        const char *v = moy_json_ws(s->msg, end);
        const char *past = moy_json_value(v, end, 0u);
        if (past != NULL && moy_json_kind(v, past) == MOY_JSON_ARR) {
            moy_json_iter_t it;
            const char *k, *ke, *e, *ee;
            moy_json_iter(&it, v, past);
            while (moy_json_next(&it, &k, &ke, &e, &ee)) {
                luaL_checkstack(L, 1, NULL);
                moy_app_lua_json_push(L, e, (size_t)(ee - e));
                n++;
            }
        }
    }
    lua_callk(L, n, 0, 0, deliver_k);
    return 0;
}

static const luaL_Reg MOYBYTE[] = {
    { "every", l_every }, { "after", l_after }, { "cancel", l_cancel },
    { "on", l_on }, { "log", l_log }, { "exit", l_exit }, { NULL, NULL },
};

static const luaL_Reg OS[] = { { "time", l_time }, { "clock", l_clock }, { NULL, NULL } };

static void open_names(lua_State *L) {
    static const luaL_Reg libs[] = {
        { LUA_GNAME, luaopen_base }, { LUA_COLIBNAME, luaopen_coroutine },
        { LUA_TABLIBNAME, luaopen_table }, { LUA_STRLIBNAME, luaopen_string },
        { LUA_MATHLIBNAME, luaopen_math },
    };
    for (size_t i = 0; i < sizeof libs / sizeof libs[0]; i++) {
        luaL_requiref(L, libs[i].name, libs[i].func, 1);
        lua_pop(L, 1);
    }
    static const char *const banned[] = { "load", "dofile", "loadfile", NULL };
    for (const char *const *b = banned; *b; b++) {
        lua_pushnil(L);
        lua_setglobal(L, *b);
    }
    lua_pushcfunction(L, l_print);
    lua_setglobal(L, "print");
    luaL_newlib(L, OS);
    lua_setglobal(L, "os");
    luaL_newlib(L, MOYBYTE);
    lua_pushinteger(L, LOG_ERR);
    lua_setfield(L, -2, "ERR");
    lua_pushinteger(L, LOG_WARNING);
    lua_setfield(L, -2, "WARNING");
    lua_pushinteger(L, LOG_INFO);
    lua_setfield(L, -2, "INFO");
#define CALL(name)                          \
    lua_pushliteral(L, "moybyte." name);    \
    lua_pushcclosure(L, l_console, 1);      \
    lua_setfield(L, -2, name);
    MOY_APP_LUA_CALLS(CALL)
#undef CALL
    lua_pushcfunction(L, l_send);       // the kernel's, not a console call
    lua_setfield(L, -2, "send");
    lua_pushcfunction(L, l_at);         // the alarm's kernel half (ALARMS)
    lua_setfield(L, -2, "at");
    lua_setglobal(L, "moybyte");
    if (moy_app_lua_open_crypto != NULL) {
        moy_app_lua_open_crypto(L);     // its run's role binding's, held with no grant
    }
}

// `moybyte.at(t, fn)`, an alarm, over its kernel half (l_at): the kernel's
// alarm posts the service the system's message "moybyte.at" with the
// alarm's id, VM or no VM, which one message wake, held while an alarm is
// due, hears. The wake is
// made before the call, so an alarm already due finds it listening. An
// alarm's id is negative, apart from the service's own wakes'; cancelling
// one cancels the kernel's alarm too (l_unalarm), giving its slot back.
static const char ALARMS[] =
    "local unalarm = ...\n"
    "local m = moybyte\n"
    "local call, on, cancel = m.at, m.on, m.cancel\n"
    "local fns, n, ear = {}, 0, nil\n"
    "local function drop() n = n - 1; if n == 0 then cancel(ear); ear = nil end end\n"
    "local function hear(_, w)\n"
    "  local fn = fns[-w]\n"
    "  if fn == nil then return end\n"
    "  fns[-w] = nil; drop(); fn()\n"
    "end\n"
    "function m.at(t, fn)\n"
    "  if type(fn) ~= 'function' then error('moybyte.at needs a function', 2) end\n"
    "  if ear == nil then ear = on('moybyte.at', hear) end\n"
    "  n = n + 1\n"
    "  local w, why = call(t)\n"
    "  if w == nil then drop(); error(why, 2) end\n"
    "  fns[-w] = fn\n"
    "  return -w\n"
    "end\n"
    "function m.cancel(id)\n"
    "  if type(id) == 'number' and id < 0 and fns[id] ~= nil then\n"
    "    fns[id] = nil; unalarm(-id); drop(); return\n"
    "  end\n"
    "  return cancel(id)\n"
    "end\n";

// The kernel's alarm `id` of the service's own cancelled (moy_evq.h, TIMERS).
static int l_unalarm(lua_State *L) {
    moy_evq_timer_cancel(of_state(L)->pid, (uint32_t)luaL_checkinteger(L, 1));
    return 0;
}

#ifdef SVC_APP
// fn(arg) on the VM's task, for a row of the app's run (moy_app_lua.h's hop).
// From the service's own task it is a hop on the VM's queue (moy_vmq.h), the
// task waiting until the VM's task has run it, the wait not counted against
// the call's budget nor its time; anywhere else (a stepped tier, the VM's
// task) fn runs in place. A row answered `here` runs in place, its time
// (the store's) not counted either.
static int vm_hop(lua_State *L, void (*fn)(void *), void *arg, int here) {
    #ifdef SVC_TASKS
    svc_t *s = of_state(L);
    if (here && xTaskGetCurrentTaskHandle() == s->task) {
        int64_t t0 = now_us();
        fn(arg);
        uint32_t took = (uint32_t)(now_us() - t0);
        s->t0 += took;
        s->seg += took;
        return 0;
    }
    if (!here && xTaskGetCurrentTaskHandle() == s->task) {
        uint32_t waited = 0;
        if (moy_vmq_hop(s->pid, fn, arg, &waited) != MOY_VMQ_OK) {
            return luaL_error(L, "the console's queue is full");
        }
        s->t0 += waited;
        s->seg += waited;
        return 0;
    }
    #else
    (void)L;
    #endif
    (void)here;
    fn(arg);
    return 0;
}
#endif

// The role names its app's run grants (moy_app_lua.h), each row hopping to
// the VM's task, with os.clock kept the service's own. Run protected.
static int l_open_run(lua_State *L) {
    #ifdef SVC_APP
    svc_t *s = of_state(L);
    if (s->run.g != 0u && moy_app_lua_open_hop != NULL
        && moy_app_lua_open_hop(L, &s->run, vm_hop)) {
        lua_getglobal(L, "os");
        lua_pushcfunction(L, l_clock);
        lua_setfield(L, -2, "clock");
        lua_pop(L, 1);
    }
    #else
    (void)L;
    #endif
    return 0;
}

// What the console hands the service, [values, calls]: each value a global,
// each call a global console call. Run protected.
static int l_open_env(lua_State *L) {
    svc_t *s = of_state(L);
    if (s->env == NULL || moy_app_lua_json_push == NULL
        || !moy_app_lua_json_push(L, s->env, s->envn) || !lua_istable(L, -1)) {
        return 0;
    }
    int env = lua_gettop(L);
    if (lua_rawgeti(L, env, 1) == LUA_TTABLE) {
        lua_pushnil(L);
        while (lua_next(L, -2) != 0) {
            if (lua_type(L, -2) == LUA_TSTRING) {
                lua_setglobal(L, lua_tostring(L, -2));
            } else {
                lua_pop(L, 1);
            }
        }
    }
    lua_settop(L, env);
    if (lua_rawgeti(L, env, 2) == LUA_TTABLE) {
        lua_Integer n = (lua_Integer)lua_rawlen(L, -1);
        for (lua_Integer i = 1; i <= n; i++) {
            if (lua_rawgeti(L, env + 1, i) == LUA_TSTRING) {
                const char *name = lua_tostring(L, -1);
                lua_pushvalue(L, -1);
                lua_pushcclosure(L, l_console, 1);
                lua_setglobal(L, name);
            }
            lua_pop(L, 1);
        }
    }
    lua_settop(L, 0);
    return 0;
}

// -- the life ------------------------------------------------------------------------

static void end(svc_t *s, int32_t code) {
    for (uint32_t i = 0; i < MOYCORE_SVC_ONS; i++) {
        if (s->on[i].id != 0) {
            moy_evq_listen(s->pid, s->on[i].name, false);
        }
    }
    moy_vmq_drop(s->pid);
    if (s->own_q) {
        moy_evq_close(s->pid);
        s->own_q = 0;
    }
    if (s->L != NULL) {
        lua_close(s->L);
        s->L = NULL;
    }
    s->co = NULL;
    for (uint32_t i = 0; i < MOYCORE_SVC_TIMERS; i++) {
        s->tm[i].id = 0;
    }
    for (uint32_t i = 0; i < MOYCORE_SVC_ONS; i++) {
        s->on[i].id = 0;
    }
    free(s->src);
    s->src = NULL;
    free(s->env);
    s->env = NULL;
    free(s->msg);
    s->msg = NULL;
    free(s->ans);
    s->ans = NULL;
    s->req = REQ_NONE;
    s->exit = code;
    s->state = MOYCORE_SVC_EXITED;
    moy_evq_want(MOY_EVQ_WANT_POLL);    // its reap is the VM's
}

static bool has_wakes(const svc_t *s) {
    for (uint32_t i = 0; i < MOYCORE_SVC_TIMERS; i++) {
        if (s->tm[i].id != 0) {
            return true;
        }
    }
    for (uint32_t i = 0; i < MOYCORE_SVC_ONS; i++) {
        if (s->on[i].id != 0) {
            return true;
        }
    }
    return false;
}

// The answer of a start's or a stop's call: true when the service ended.
static bool ended_by(svc_t *s, int rc, const char *past) {
    if (rc == CALL_OK) {
        return false;
    }
    if (rc == CALL_EXIT) {
        end(s, s->exit_req);
    } else if (rc == CALL_ABORT) {
        snprintf(s->why, sizeof s->why, "%s ran past %u ms", past, MOYCORE_SVC_STOP_MS);
        end(s, MOY_PROC_KILLED);
    } else {
        end(s, MOYCORE_SVC_EXIT_CRASHED);
    }
    return true;
}

static void started(svc_t *s) {
    if (!has_wakes(s)) {
        end(s, 0);
        return;
    }
    s->state = MOYCORE_SVC_RUNNING;
}

static void after(svc_t *s, int rc);

// A call begun; what follows it once it ends, now or after its waits.
static void begin(svc_t *s, uint32_t budget, int nargs, uint8_t then) {
    int rc = call(s, budget, nargs, then);
    if (rc != CALL_WAIT) {
        after(s, rc);
    }
}

static void after(svc_t *s, int rc) {
    switch (s->then) {
        case THEN_CHUNK:
            if (ended_by(s, rc, "its start")) {
                return;
            }
            if (lua_getglobal(s->L, "moy_start") == LUA_TFUNCTION) {
                begin(s, MOYCORE_SVC_STOP_MS * 1000u, 0, THEN_START);
            } else {
                lua_settop(s->L, 0);
                started(s);
            }
            return;
        case THEN_START:
            if (!ended_by(s, rc, "its start")) {
                started(s);
            }
            return;
        case THEN_STOP:
            if (!ended_by(s, rc, "its stop")) {
                end(s, 0);
            }
            return;
        default:
            break;
    }
    if (rc == CALL_EXIT) {
        end(s, s->exit_req);
    } else if (rc == CALL_ERR) {
        end(s, MOYCORE_SVC_EXIT_CRASHED);
    } else if (rc == CALL_ABORT || s->spent > (int64_t)MOY_SUPER_SLICE_US) {
        // the one supervisor's rungs (moy_super.h): a suspension holds its
        // wakes unserved until it is resumed
        moy_super_charge(s->pid, (uint32_t)s->spent, rc == CALL_ABORT);
    }
}

// A service's scripts as its start keeps them, one block: per script its
// chunk's name (NUL-ended, empty for the service's own `=id`), its length
// (4 bytes) and its text.
static char *pack_chunks(const moycore_svc_chunk_t *chunks, size_t count, size_t *out) {
    size_t n = 0;
    for (size_t i = 0; i < count; i++) {
        n += (chunks[i].name != NULL ? strlen(chunks[i].name) : 0u) + 1u + 4u + chunks[i].n;
    }
    char *p = mem_realloc(NULL, n + 1u);
    if (p == NULL) {
        return NULL;
    }
    char *at = p;
    for (size_t i = 0; i < count; i++) {
        size_t k = chunks[i].name != NULL ? strlen(chunks[i].name) : 0u;
        memcpy(at, chunks[i].name != NULL ? chunks[i].name : "", k + 1u);
        at += k + 1u;
        uint32_t len = (uint32_t)chunks[i].n;
        memcpy(at, &len, 4u);
        at += 4u;
        if (len) {
            memcpy(at, chunks[i].src, len);
        }
        at += len;
    }
    *out = n;
    return p;
}

// Runs the scripts it is handed, in order: every chunk's own locals stay its
// own, and they share through globals, as a window's scripts do.
static const char CHUNKS[] =
    "local f = {...}\n"
    "return function() for i = 1, #f do f[i]() end end\n";

// Its scripts loaded: LUA_OK with the one function its start runs pushed,
// else the error pushed.
static int load_chunks(svc_t *s) {
    int count = 0;
    for (size_t at = 0; at < s->n; count++) {
        const char *name = s->src + at;
        at += strlen(name) + 1u;
        uint32_t len;
        memcpy(&len, s->src + at, 4u);
        at += 4u;
        if (!lua_checkstack(s->L, 2)) {
            return LUA_ERRMEM;          // no error pushed: "it does not load"
        }
        if (count == 0 && at + len < s->n) {
            int rc = luaL_loadbufferx(s->L, CHUNKS, sizeof CHUNKS - 1u, "=chunks", "t");
            if (rc != LUA_OK) {
                return rc;
            }
        }
        int rc = luaL_loadbufferx(s->L, s->src + at, len, *name ? name : s->id, "t");
        if (rc != LUA_OK) {
            return rc;
        }
        at += len;
    }
    return count > 1 ? lua_pcall(s->L, count, 1, 0) : LUA_OK;
}

static void start(svc_t *s) {
    s->L = lua_newstate(svc_alloc, s);
    if (s->L == NULL) {
        snprintf(s->why, sizeof s->why, "no memory for its state");
        end(s, MOYCORE_SVC_EXIT_CRASHED);
        return;
    }
    *(svc_t **)lua_getextraspace(s->L) = s;
    open_names(s->L);
    if (luaL_loadbufferx(s->L, ALARMS, sizeof ALARMS - 1u, "=alarms", "t") != LUA_OK
        || (lua_pushcfunction(s->L, l_unalarm), lua_pcall(s->L, 1, 0, 0)) != LUA_OK) {
        snprintf(s->why, sizeof s->why, "its alarms do not open");
        end(s, MOYCORE_SVC_EXIT_CRASHED);
        return;
    }
    lua_pushcfunction(s->L, l_open_run);
    if (lua_pcall(s->L, 0, 0, 0) != LUA_OK) {
        const char *m = lua_tostring(s->L, -1);
        snprintf(s->why, sizeof s->why, "%s", m != NULL ? m : "its roles do not open");
        end(s, MOYCORE_SVC_EXIT_CRASHED);
        return;
    }
    lua_pushcfunction(s->L, l_open_env);
    if (lua_pcall(s->L, 0, 0, 0) != LUA_OK) {
        const char *m = lua_tostring(s->L, -1);
        snprintf(s->why, sizeof s->why, "%s", m != NULL ? m : "its names do not open");
        end(s, MOYCORE_SVC_EXIT_CRASHED);
        return;
    }
    free(s->env);
    s->env = NULL;
    if (load_chunks(s) != LUA_OK) {
        const char *m = lua_tostring(s->L, -1);
        snprintf(s->why, sizeof s->why, "%s", m != NULL ? m : "it does not load");
        end(s, MOYCORE_SVC_EXIT_CRASHED);
        return;
    }
    free(s->src);
    s->src = NULL;
    begin(s, MOYCORE_SVC_STOP_MS * 1000u, 0, THEN_CHUNK);
}

static void stop(svc_t *s) {
    s->state = MOYCORE_SVC_STOPPING;
    if (lua_getglobal(s->L, "moy_stop") == LUA_TFUNCTION) {
        begin(s, MOYCORE_SVC_STOP_MS * 1000u, 0, THEN_STOP);
        return;
    }
    end(s, 0);
}

// The next delivery of the message under way to a wake on its name, or of
// the oldest one on its event queue: its delivery and its handler pushed on
// the main state, the message held for it: 1, or 0 for none.
static int take_mail(svc_t *s) {
    for (;;) {
        while (s->msg != NULL && s->msg_next < MOYCORE_SVC_ONS) {
            svc_on_t *o = &s->on[s->msg_next++];
            if (o->id != 0 && strcmp(o->name, s->ev.name) == 0) {
                lua_pushcfunction(s->L, l_deliver);
                lua_rawgeti(s->L, LUA_REGISTRYINDEX, o->ref);
                return 1;
            }
        }
        free(s->msg);
        s->msg = NULL;
        char small[256];
        int rc = moy_evq_take(s->pid, &s->ev, small, sizeof small);
        if (rc == MOY_EVQ_RANGE) {
            char *big = mem_realloc(NULL, s->ev.n + 1u);
            if (big == NULL) {
                return 0;
            }
            if (moy_evq_take(s->pid, &s->ev, big, s->ev.n + 1u) != MOY_EVQ_OK) {
                free(big);
                return 0;
            }
            s->msg = big;
        } else if (rc == MOY_EVQ_OK) {
            s->msg = mem_copy(small, s->ev.n);
            if (s->msg == NULL) {
                return 0;
            }
        } else {
            return 0;
        }
        s->msg_n = s->ev.n;
        s->msg_next = 0;
    }
}

// What is due on `s` at `now`; the ms to its next deadline (UINT32_MAX while
// a call waits on the console).
static uint32_t run(svc_t *s, uint32_t now) {
    s->now = now;
    if (s->co != NULL) {
        if (s->req == REQ_POSTED) {
            char *ans = NULL;
            size_t n = 0;
            int got = moy_vmq_result(s->ticket, &ans, &n);
            if (got == MOY_VMQ_WAITING) {
                return UINT32_MAX;
            }
            s->ans = got == MOY_VMQ_OK ? ans : NULL;
            s->ans_n = n;
        }
        s->req = REQ_NONE;
        int rc = resume(s, 0);
        if (rc != CALL_WAIT) {
            after(s, rc);
        }
        if (s->co != NULL) {
            return UINT32_MAX;
        }
    }
    if (s->state == MOYCORE_SVC_STARTING && s->L == NULL) {
        start(s);
        if (s->co != NULL) {
            return UINT32_MAX;
        }
    }
    if (s->state == MOYCORE_SVC_RUNNING && s->stop_req) {
        stop(s);
        if (s->co != NULL) {
            return UINT32_MAX;
        }
    }
    if (s->state != MOYCORE_SVC_RUNNING || moy_super_held(s->pid)) {
        return UINT32_MAX;
    }
    for (uint32_t i = 0; i < MOYCORE_SVC_TIMERS && s->state == MOYCORE_SVC_RUNNING; i++) {
        svc_timer_t *t = &s->tm[i];
        if (t->id == 0 || (int32_t)(now - t->due) < 0) {
            continue;
        }
        lua_rawgeti(s->L, LUA_REGISTRYINDEX, t->ref);
        if (t->every != 0) {
            t->due += t->every;
            if ((int32_t)(now - t->due) >= 0) {
                t->due = now + t->every;
            }
        } else {
            timer_end(s, t);
        }
        begin(s, MOY_SUPER_ABORT_US, 0, THEN_CB);
        if (s->co != NULL) {
            return UINT32_MAX;
        }
    }
    if (s->msg == NULL) {
        moy_evq_step(s->pid);           // a run hands MOY_EVQ_BATCH messages at most
    }
    while (s->state == MOYCORE_SVC_RUNNING && !s->stop_req && !moy_super_held(s->pid)
           && take_mail(s)) {
        begin(s, MOY_SUPER_ABORT_US, 1, THEN_CB);
        if (s->co != NULL) {
            return UINT32_MAX;
        }
    }
    if (s->state != MOYCORE_SVC_RUNNING) {
        return UINT32_MAX;
    }
    if (s->stop_req
        || (s->msg == NULL && moy_evq_queued(s->pid) != 0u && !moy_super_held(s->pid))) {
        return 0;                       // the next run hands the rest
    }
    if (!has_wakes(s)) {
        end(s, 0);
        return UINT32_MAX;
    }
    uint32_t wait = UINT32_MAX;
    for (uint32_t i = 0; i < MOYCORE_SVC_TIMERS; i++) {
        if (s->tm[i].id != 0) {
            int32_t d = (int32_t)(s->tm[i].due - now);
            uint32_t w = d > 0 ? (uint32_t)d : 0u;
            wait = w < wait ? w : wait;
        }
    }
    return wait;
}

#ifdef SVC_TASKS
static void svc_task(void *arg) {
    svc_t *s = arg;
    for (;;) {
        uint32_t wait = run(s, (uint32_t)(now_us() / 1000));
        if (s->state == MOYCORE_SVC_EXITED) {
            break;
        }
        if (wait > 1000u) {
            wait = 1000u;
        }
        moy_evq_wait(s->pid, wait > 0 ? wait : 1u);     // the one kernel wait
    }
    moy_evq_unwait(s->pid);             // the queue may outlive this task
    #ifdef SVC_STACK_PSRAM
    // A task made with its caps is deleted by another (row_free): its own
    // delete would make a helper task, in internal SRAM, to free its stack.
    s->done = 1;
    for (;;) {
        vTaskSuspend(NULL);
    }
    #else
    s->done = 1;
    vTaskDelete(NULL);
    #endif
}
#endif

static void wake(svc_t *s) {
    moy_evq_kick(s->pid);
}

// -- the table -----------------------------------------------------------------------

static svc_t *row_of(uint32_t h) {
    uint32_t slot = (h & 0xFFu) - 1u;
    if (h == 0 || slot >= MOYCORE_SVCS || s_row[slot] == NULL
        || s_row[slot]->gen != (uint16_t)(h >> 8)) {
        return NULL;
    }
    return s_row[slot];
}

// A row whose service has exited and whose task is done, freed with its
// task where a reaper deletes it.
static void row_free(uint32_t slot) {
    svc_t *s = s_row[slot];
    row_put(slot, NULL);
    #ifdef SVC_STACK_PSRAM
    if (s->task != NULL) {
        vTaskDeleteWithCaps(s->task);
    }
    #endif
    free(s);
}

// A done row no scheduler will reap (its VM stopped), freed with the event
// queue that scheduler opened for it.
static void orphan_free(uint32_t slot) {
    uint32_t pid = s_row[slot]->pid;
    row_free(slot);
    if (moy_evq_has(pid)) {
        moy_evq_close(pid);
    }
}

static uint32_t handle_of(uint32_t slot) {
    return s_row[slot] != NULL ? ((uint32_t)s_row[slot]->gen << 8) | (slot + 1u) : 0u;
}

int moycore_svc_start(uint32_t pid, const char *id, const moycore_svc_chunk_t *chunks,
                      size_t count, const char *env, size_t envn,
                      const struct moy_app_run *app, uint32_t *h) {
    if (count == 0) {
        return MOYCORE_SVC_BAD;
    }
    uint32_t slot = 0;
    while (slot < MOYCORE_SVCS && s_row[slot] != NULL
           && !(s_row[slot]->orphan && s_row[slot]->done)) {
        slot++;
    }
    if (slot == MOYCORE_SVCS) {
        return MOYCORE_SVC_FULL;
    }
    if (s_row[slot] != NULL) {
        orphan_free(slot);
    }
    svc_t *s = mem_calloc(sizeof(svc_t));
    size_t n = 0;
    char *copy = pack_chunks(chunks, count, &n);
    char *envc = env != NULL ? mem_copy(env, envn) : NULL;
    if (s == NULL || copy == NULL || (env != NULL && envc == NULL)) {
        free(s);
        free(copy);
        free(envc);
        return MOYCORE_SVC_NOMEM;
    }
    s->src = copy;
    s->n = n;
    s->env = envc;
    s->envn = envn;
    s->pid = pid;
    #ifdef SVC_APP
    if (app != NULL) {
        s->run = *app;
    }
    #else
    (void)app;
    #endif
    snprintf(s->id, sizeof s->id, "=%s", id != NULL ? id : "service");
    if (++s_gen == 0) {
        s_gen = 1;
    }
    s->gen = s_gen;
    s->state = MOYCORE_SVC_STARTING;
    if (!moy_evq_has(pid)) {
        if (moy_evq_open(pid, id != NULL ? id : "service", NULL, 0u) != MOY_EVQ_OK) {
            free(copy);
            free(envc);
            free(s);
            return MOYCORE_SVC_NOMEM;
        }
        s->own_q = 1;
    }
    row_put(slot, s);
    #ifdef SVC_TASKS
    #ifdef SVC_STACK_PSRAM
    BaseType_t made = xTaskCreatePinnedToCoreWithCaps(svc_task, "moy_svc", MOYCORE_SVC_STACK, s,
                                                      MOYCORE_SVC_PRIO, &s->task, SVC_CORE,
                                                      MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    #else
    BaseType_t made = xTaskCreatePinnedToCore(svc_task, "moy_svc", MOYCORE_SVC_STACK, s,
                                              MOYCORE_SVC_PRIO, &s->task, SVC_CORE);
    #endif
    if (made != pdPASS) {
        row_put(slot, NULL);
        if (s->own_q) {
            moy_evq_close(pid);
        }
        free(copy);
        free(envc);
        free(s);
        return MOYCORE_SVC_NOMEM;
    }
    #else
    s->done = 1;
    #endif
    *h = handle_of(slot);
    return MOYCORE_SVC_OK;
}

int moycore_svc_stop(uint32_t h) {
    svc_t *s = row_of(h);
    if (s == NULL) {
        return MOYCORE_SVC_STALE;
    }
    s->stop_req = 1;
    wake(s);
    return MOYCORE_SVC_OK;
}

int moycore_svc_info(uint32_t h, moycore_svc_info_t *out) {
    svc_t *s = row_of(h);
    if (s == NULL) {
        return MOYCORE_SVC_STALE;
    }
    uint32_t overruns, strikes;
    moy_super_info(s->pid, &overruns, &strikes);
    out->state = s->state;
    out->strikes = (uint8_t)strikes;
    out->exit = s->state == MOYCORE_SVC_EXITED ? s->exit : 0;
    out->heap = s->heap;
    out->cpu_us = s->cpu_us;
    out->pid = s->pid;
    if (s->state == MOYCORE_SVC_EXITED) {
        memcpy(out->why, s->why, sizeof out->why);
    } else {
        out->why[0] = 0;
    }
    uint8_t w = 0;
    for (uint32_t i = 0; i < MOYCORE_SVC_TIMERS; i++) {
        w = (uint8_t)(w + (s->tm[i].id != 0));
    }
    for (uint32_t i = 0; i < MOYCORE_SVC_ONS; i++) {
        w = (uint8_t)(w + (s->on[i].id != 0));
    }
    out->wakes = w;
    out->mail = (uint8_t)moy_evq_queued(s->pid);
    return MOYCORE_SVC_OK;
}

int moycore_svc_reap(uint32_t h) {
    svc_t *s = row_of(h);
    if (s == NULL) {
        return MOYCORE_SVC_STALE;
    }
    if (s->state != MOYCORE_SVC_EXITED || !s->done) {
        return MOYCORE_SVC_BAD;
    }
    row_free((h & 0xFFu) - 1u);
    return MOYCORE_SVC_OK;
}

uint32_t moycore_svc_at(uint32_t slot) {
    return slot < MOYCORE_SVCS ? handle_of(slot) : 0u;
}

bool moycore_svc_tasks(void) {
    #ifdef SVC_TASKS
    return true;
    #else
    return false;
    #endif
}

static uint32_t svc_find(const char *id, size_t n, bool term) {
    uint32_t pid = 0u;
    if (n + 1u >= sizeof ((svc_t *)0)->id) {
        return 0u;
    }
    if (moy_lock_take != NULL) {
        moy_lock_take();
    }
    for (uint32_t i = 0; i < MOYCORE_SVCS && pid == 0u; i++) {
        svc_t *s = s_row[i];
        if (s != NULL && !s->stop_req && !s->orphan && !s->exiting
            && (s->state == MOYCORE_SVC_STARTING || s->state == MOYCORE_SVC_RUNNING)
            && strncmp(s->id + 1, id, n) == 0 && s->id[n + 1u] == 0) {
            pid = s->pid;
            if (term) {
                s->stop_req = 1;
            }
        }
    }
    if (moy_lock_give != NULL) {
        moy_lock_give();
    }
    if (term && pid != 0u) {
        moy_evq_kick(pid);
    }
    return pid;
}

uint32_t moycore_svc_of(const char *id, size_t n) {
    return svc_find(id, n, false);
}

uint32_t moycore_svc_term(const char *id, size_t n) {
    return svc_find(id, n, true);
}

uint32_t moycore_svc_step(uint32_t now_ms) {
    uint32_t wait = UINT32_MAX;
    #ifndef SVC_TASKS
    for (uint32_t i = 0; i < MOYCORE_SVCS; i++) {
        svc_t *s = s_row[i];
        if (s != NULL && s->state != MOYCORE_SVC_EXITED) {
            uint32_t w = run(s, now_ms);
            wait = w < wait ? w : wait;
        }
    }
    #else
    (void)now_ms;
    #endif
    return wait;
}

void moycore_svc_reset(void) {
    #ifndef SVC_TASKS
    for (uint32_t i = 0; i < MOYCORE_SVCS; i++) {
        svc_t *s = s_row[i];
        if (s != NULL) {
            if (s->state != MOYCORE_SVC_EXITED) {
                end(s, MOY_PROC_KILLED);
            }
            row_put(i, NULL);
            free(s);
        }
    }
    #endif
}

void moycore_svc_vm_stop(bool keep) {
    #ifdef SVC_TASKS
    if (keep) {
        return;
    }
    // A fresh start follows: each service ends now. No console answers a
    // call any more, so each is refused, and the hops its role rows wait on
    // are served here, on the VM's task while the VM still stands.
    int64_t until = now_us() + (int64_t)(MOYCORE_SVC_STOP_MS + 500u) * 1000;
    for (;;) {
        bool live = false;
        for (uint32_t i = 0; i < MOYCORE_SVCS; i++) {
            svc_t *s = s_row[i];
            if (s != NULL && !s->done) {
                s->stop_req = 1;
                moy_vmq_drop(s->pid);
                wake(s);
                live = true;
            }
        }
        if (!live || now_us() >= until) {
            break;
        }
        moy_vmq_serve();
        vTaskDelay(1);
    }
    // No reaper outlives the VM: each row and the queue its scheduler opened
    // go now, and one still running goes at the next start once it is done.
    for (uint32_t i = 0; i < MOYCORE_SVCS; i++) {
        svc_t *s = s_row[i];
        if (s == NULL) {
            continue;
        }
        if (s->done) {
            orphan_free(i);
        } else {
            s->orphan = 1;
        }
    }
    #else
    (void)keep;
    moycore_svc_reset();
    #endif
}

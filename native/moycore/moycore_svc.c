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
#include "moy_json.h"

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
#if CONFIG_IDF_TARGET_ESP32P4
#define SVC_TASKS 1
#else
#define SVC_NONE 1              // the S3s: no user service in Lua
#endif
#endif

// The OS log (native/moy_kernel/moy_log.h), where the image has it.
void moy_log_write(int level, uint32_t pid, const char *text, size_t n) __attribute__((weak));
enum { LOG_ERR = 3, LOG_WARNING = 4, LOG_INFO = 6 };

// The prefs names' JSON (native/moy_app/moy_app_lua.h), where the image has
// the app ABI: a message's arguments, the console's names and its answers.
int moy_app_lua_json_push(lua_State *L, const char *text, size_t n) __attribute__((weak));
const char *moy_app_lua_json_dump(lua_State *L, int idx, size_t *n) __attribute__((weak));

// The role names of its app's run (moy_app_lua.h), where the image has the
// app ABI.
#if defined(__has_include)
#if __has_include("moy_app_run.h")
#include "moy_app_run.h"
#define SVC_APP 1
int moy_app_lua_open_hop(lua_State *L, const struct moy_app_run *run,
                         int (*hop)(lua_State *L, void (*fn)(void *), void *arg))
    __attribute__((weak));
#endif
#endif

#ifdef SVC_NONE

// The S3s: no user service in Lua, and no table in their internal RAM.
int moycore_svc_start(uint32_t pid, const char *id, const char *src, size_t n,
                      const char *env, size_t envn, const struct moy_app_run *app,
                      uint32_t *h) {
    (void)pid;
    (void)id;
    (void)src;
    (void)n;
    (void)env;
    (void)envn;
    (void)app;
    (void)h;
    return MOYCORE_SVC_BAD;
}

int moycore_svc_send(uint32_t h, const char *name, const char *args, size_t n) {
    (void)h;
    (void)name;
    (void)args;
    (void)n;
    return MOYCORE_SVC_STALE;
}

int moycore_svc_call(uint32_t h, const char **name, const char **args, size_t *n) {
    (void)h;
    (void)name;
    (void)args;
    (void)n;
    return MOYCORE_SVC_STALE;
}

int moycore_svc_answer(uint32_t h, const char *ans, size_t n) {
    (void)h;
    (void)ans;
    (void)n;
    return MOYCORE_SVC_STALE;
}

int moycore_svc_stop(uint32_t h) {
    (void)h;
    return MOYCORE_SVC_STALE;
}

int moycore_svc_info(uint32_t h, moycore_svc_info_t *out) {
    (void)h;
    (void)out;
    return MOYCORE_SVC_STALE;
}

int moycore_svc_reap(uint32_t h) {
    (void)h;
    return MOYCORE_SVC_STALE;
}

uint32_t moycore_svc_at(uint32_t slot) {
    (void)slot;
    return 0;
}

bool moycore_svc_tasks(void) {
    return false;
}

uint32_t moycore_svc_step(uint32_t now_ms) {
    (void)now_ms;
    return UINT32_MAX;
}

void moycore_svc_reset(void) {
}

#else

#define HOOK_COUNT 1000         // instructions between the hook's looks at the clock

enum { CALL_OK, CALL_ERR, CALL_ABORT, CALL_EXIT, CALL_WAIT };
// What follows a call's end.
enum { THEN_CHUNK, THEN_START, THEN_STOP, THEN_CB };
// A console call's passage: posted by the service, answered by the VM.
enum { REQ_NONE, REQ_POSTED, REQ_ANSWERED };
// A hop's: posted by the service's task, run by the VM's.
enum { HOP_NONE, HOP_POSTED, HOP_DONE };

static const char *const MESSAGES[] = { MOYCORE_SVC_MESSAGES };

typedef struct {
    uint32_t id;                // 0: a free timer
    uint32_t due;               // ms
    uint32_t every;             // 0: once
    int ref;                    // the callback, in the registry
} svc_timer_t;

typedef struct {
    uint32_t id;                // 0: a free wake; written last, read by the VM's send
    int ref;                    // the handler, in the registry
    char name[MOYCORE_SVC_NAME];
} svc_on_t;

typedef struct {
    char *args;                 // a JSON array
    uint32_t n;
    uint32_t wid;               // the wake it was queued on
} svc_msg_t;

typedef struct {
    lua_State *L;
    char *src;
    size_t n;
    char *env;                  // what the console hands it, until its start reads it
    size_t envn;
    uint32_t pid;
    uint16_t gen;
    volatile uint8_t state;
    volatile uint8_t strikes;
    volatile uint8_t stop_req;
    volatile uint8_t done;      // its task is gone (or it never had one)
    volatile int32_t exit;
    volatile uint32_t heap;
    volatile uint32_t cpu_us;
    uint32_t now;               // the step's clock, ms
    uint32_t next_id;
    svc_timer_t tm[MOYCORE_SVC_TIMERS];
    svc_on_t on[MOYCORE_SVC_ONS];
    // the mail: a ring, its oldest at mail_at; taken under the lock
    svc_msg_t mail[MOYCORE_SVC_MAIL];
    uint8_t mail_at;
    uint8_t mail_n;
    uint8_t sealed;             // ended: it takes no more
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
    char *msg;                  // the message it delivers, until its handler has its arguments
    size_t msg_n;
    // its console call
    uint8_t req;                // REQ_*, atomically
    char req_name[MOYCORE_SVC_NAME];
    char *req_args;
    size_t req_n;
    char *ans;                  // the answer, NULL: refused
    size_t ans_n;
    char id[32];
    char why[MOYCORE_SVC_WHY];
#ifdef SVC_APP
    moy_app_run_t run;          // its app's run, g 0 for none
#endif
#ifdef SVC_TASKS
    TaskHandle_t task;
    // the hop under way: posted by its task, run by the VM's (moycore_svc_call)
    uint8_t hop;                // HOP_*, atomically
    void (*hop_fn)(void *);
    void *hop_arg;
#endif
} svc_t;

static svc_t *s_row[MOYCORE_SVCS];
static uint16_t s_gen;

#ifdef SVC_TASKS
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
#define MAIL_LOCK() taskENTER_CRITICAL(&s_mux)
#define MAIL_UNLOCK() taskEXIT_CRITICAL(&s_mux)
#else
#define MAIL_LOCK() ((void)0)
#define MAIL_UNLOCK() ((void)0)
#endif

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

static void say(svc_t *s, int level, const char *text) {
    if (moy_log_write != NULL) {
        moy_log_write(level, s->pid, text, strlen(text));
    }
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
    } else if (rc == LUA_YIELD && __atomic_load_n(&s->req, __ATOMIC_ACQUIRE) == REQ_POSTED) {
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
    __atomic_store_n(&o->id, 0u, __ATOMIC_RELEASE);
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
    int known = 0;
    for (size_t i = 0; i < sizeof MESSAGES / sizeof MESSAGES[0]; i++) {
        known = known || strcmp(MESSAGES[i], name) == 0;
    }
    if (!known || n >= MOYCORE_SVC_NAME) {
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
    lua_pushvalue(L, 2);
    o->ref = luaL_ref(L, LUA_REGISTRYINDEX);
    memcpy(o->name, name, n + 1u);
    uint32_t id = new_id(s);
    __atomic_store_n(&o->id, id, __ATOMIC_RELEASE);
    lua_pushinteger(L, (lua_Integer)id);
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
    char *args = mem_copy(text, len);
    if (args == NULL) {
        return luaL_error(L, "no memory for a console call");
    }
    snprintf(s->req_name, sizeof s->req_name, "%s", lua_tostring(L, lua_upvalueindex(1)));
    s->req_args = args;
    s->req_n = len;
    lua_settop(L, 0);
    __atomic_store_n(&s->req, (uint8_t)REQ_POSTED, __ATOMIC_RELEASE);
    return lua_yieldk(L, 0, 0, console_k);
}

static int deliver_k(lua_State *L, int status, lua_KContext ctx) {
    (void)L;
    (void)status;
    (void)ctx;
    return 0;
}

// A message delivered: its handler (at 1) called with the arguments its JSON
// array holds, each in its place, a null as nil.
static int l_deliver(lua_State *L) {
    svc_t *s = of_state(L);
    int n = 0;
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
    free(s->msg);
    s->msg = NULL;
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
    lua_pushnil(L);
    lua_setglobal(L, "dofile");
    lua_pushnil(L);
    lua_setglobal(L, "loadfile");
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
    lua_pushliteral(L, "moybyte.notify");
    lua_pushcclosure(L, l_console, 1);
    lua_setfield(L, -2, "notify");
    lua_pushliteral(L, "moybyte.open_with");
    lua_pushcclosure(L, l_console, 1);
    lua_setfield(L, -2, "open_with");
    lua_pushliteral(L, "moybyte.share");
    lua_pushcclosure(L, l_console, 1);
    lua_setfield(L, -2, "share");
    lua_pushliteral(L, "moybyte.tell");
    lua_pushcclosure(L, l_console, 1);
    lua_setfield(L, -2, "tell");
    lua_setglobal(L, "moybyte");
}

#ifdef SVC_APP
// fn(arg) on the VM's task, for a row of the app's run (moy_app_lua.h's hop).
// From the service's own task the hop is posted on its row and the task waits
// until the VM's task has run it (moycore_svc_call), the wait not counted
// against the call's budget nor its time; anywhere else (a stepped tier, the
// VM's task) fn runs in place.
static int vm_hop(lua_State *L, void (*fn)(void *), void *arg) {
    #ifdef SVC_TASKS
    svc_t *s = of_state(L);
    if (xTaskGetCurrentTaskHandle() == s->task) {
        s->hop_fn = fn;
        s->hop_arg = arg;
        int64_t t = now_us();
        __atomic_store_n(&s->hop, (uint8_t)HOP_POSTED, __ATOMIC_RELEASE);
        while (__atomic_load_n(&s->hop, __ATOMIC_ACQUIRE) != HOP_DONE) {
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
        }
        __atomic_store_n(&s->hop, (uint8_t)HOP_NONE, __ATOMIC_RELEASE);
        int64_t waited = now_us() - t;
        s->t0 += waited;
        s->seg += waited;
        xTaskNotifyGive(s->task);      // a wake the wait took is the loop's again
        return 0;
    }
    #else
    (void)L;
    #endif
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
    svc_msg_t left[MOYCORE_SVC_MAIL];
    uint32_t n = 0;
    MAIL_LOCK();
    s->sealed = 1;
    while (s->mail_n) {
        left[n++] = s->mail[s->mail_at];
        s->mail_at = (uint8_t)((s->mail_at + 1u) % MOYCORE_SVC_MAIL);
        s->mail_n--;
    }
    MAIL_UNLOCK();
    for (uint32_t i = 0; i < n; i++) {
        free(left[i].args);
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
        __atomic_store_n(&s->on[i].id, 0u, __ATOMIC_RELEASE);
    }
    free(s->src);
    s->src = NULL;
    free(s->env);
    s->env = NULL;
    free(s->msg);
    s->msg = NULL;
    free(s->req_args);
    s->req_args = NULL;
    free(s->ans);
    s->ans = NULL;
    s->req = REQ_NONE;
    s->exit = code;
    s->state = MOYCORE_SVC_EXITED;
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
        end(s, MOYCORE_SVC_EXIT_KILLED);
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
    } else if (rc == CALL_ABORT) {
        s->strikes++;
        char line[64];
        snprintf(line, sizeof line, "a callback ran past %u ms: strike %u",
                 MOYCORE_SVC_ABORT_US / 1000u, (unsigned)s->strikes);
        say(s, LOG_WARNING, line);
        if (s->strikes >= MOYCORE_SVC_STRIKES) {
            snprintf(s->why, sizeof s->why, "struck out");
            end(s, MOYCORE_SVC_EXIT_KILLED);
        }
    }
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
    if (luaL_loadbufferx(s->L, s->src, s->n, s->id, "t") != LUA_OK) {
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

// The oldest message whose wake stands, its delivery and its handler pushed
// on the main state and its arguments held for it: 1, or 0 for none.
static int take_mail(svc_t *s) {
    for (;;) {
        MAIL_LOCK();
        if (s->mail_n == 0) {
            MAIL_UNLOCK();
            return 0;
        }
        svc_msg_t m = s->mail[s->mail_at];
        s->mail_at = (uint8_t)((s->mail_at + 1u) % MOYCORE_SVC_MAIL);
        s->mail_n--;
        MAIL_UNLOCK();
        svc_on_t *o = on_of(s, m.wid);
        if (o == NULL) {
            free(m.args);
            continue;
        }
        s->msg = m.args;
        s->msg_n = m.n;
        lua_pushcfunction(s->L, l_deliver);
        lua_rawgeti(s->L, LUA_REGISTRYINDEX, o->ref);
        return 1;
    }
}

// What is due on `s` at `now`; the ms to its next deadline (UINT32_MAX while
// a call waits on the console).
static uint32_t run(svc_t *s, uint32_t now) {
    s->now = now;
    if (s->co != NULL) {
        if (__atomic_load_n(&s->req, __ATOMIC_ACQUIRE) != REQ_ANSWERED) {
            return UINT32_MAX;
        }
        free(s->req_args);
        s->req_args = NULL;
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
    if (s->state != MOYCORE_SVC_RUNNING) {
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
        begin(s, MOYCORE_SVC_ABORT_US, 0, THEN_CB);
        if (s->co != NULL) {
            return UINT32_MAX;
        }
    }
    while (s->state == MOYCORE_SVC_RUNNING && !s->stop_req && take_mail(s)) {
        begin(s, MOYCORE_SVC_ABORT_US, 1, THEN_CB);
        if (s->co != NULL) {
            return UINT32_MAX;
        }
    }
    if (s->state != MOYCORE_SVC_RUNNING) {
        return UINT32_MAX;
    }
    if (s->stop_req) {
        return 0;
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
        TickType_t ticks = pdMS_TO_TICKS(wait);
        ulTaskNotifyTake(pdTRUE, ticks > 0 ? ticks : 1);
    }
    s->done = 1;
    vTaskDelete(NULL);
}
#endif

static void wake(svc_t *s) {
    #ifdef SVC_TASKS
    if (!s->done) {
        xTaskNotifyGive(s->task);
    }
    #else
    (void)s;
    #endif
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

static uint32_t handle_of(uint32_t slot) {
    return s_row[slot] != NULL ? ((uint32_t)s_row[slot]->gen << 8) | (slot + 1u) : 0u;
}

int moycore_svc_start(uint32_t pid, const char *id, const char *src, size_t n,
                      const char *env, size_t envn, const struct moy_app_run *app,
                      uint32_t *h) {
    uint32_t slot = 0;
    while (slot < MOYCORE_SVCS && s_row[slot] != NULL) {
        slot++;
    }
    if (slot == MOYCORE_SVCS) {
        return MOYCORE_SVC_FULL;
    }
    svc_t *s = mem_calloc(sizeof(svc_t));
    char *copy = mem_copy(src, n);
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
    s_row[slot] = s;
    #ifdef SVC_TASKS
    if (xTaskCreatePinnedToCore(svc_task, "moy_svc", MOYCORE_SVC_STACK, s, MOYCORE_SVC_PRIO,
                                &s->task, MOYCORE_SVC_CORE) != pdPASS) {
        s_row[slot] = NULL;
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

int moycore_svc_send(uint32_t h, const char *name, const char *args, size_t n) {
    svc_t *s = row_of(h);
    if (s == NULL) {
        return MOYCORE_SVC_STALE;
    }
    int took = 0;
    for (uint32_t i = 0; i < MOYCORE_SVC_ONS; i++) {
        uint32_t wid = __atomic_load_n(&s->on[i].id, __ATOMIC_ACQUIRE);
        if (wid == 0 || strcmp(s->on[i].name, name) != 0) {
            continue;
        }
        char *copy = mem_copy(args, n);
        if (copy == NULL) {
            return MOYCORE_SVC_NOMEM;
        }
        svc_msg_t old = { NULL, 0, 0 };
        MAIL_LOCK();
        if (s->sealed) {
            MAIL_UNLOCK();
            free(copy);
            return MOYCORE_SVC_BAD;
        }
        if (s->mail_n == MOYCORE_SVC_MAIL) {
            old = s->mail[s->mail_at];
            s->mail_at = (uint8_t)((s->mail_at + 1u) % MOYCORE_SVC_MAIL);
            s->mail_n--;
        }
        svc_msg_t *m = &s->mail[(s->mail_at + s->mail_n) % MOYCORE_SVC_MAIL];
        m->args = copy;
        m->n = (uint32_t)n;
        m->wid = wid;
        s->mail_n++;
        MAIL_UNLOCK();
        free(old.args);
        took = 1;
    }
    if (!took) {
        return MOYCORE_SVC_BAD;
    }
    wake(s);
    return MOYCORE_SVC_OK;
}

int moycore_svc_call(uint32_t h, const char **name, const char **args, size_t *n) {
    svc_t *s = row_of(h);
    if (s == NULL) {
        return MOYCORE_SVC_STALE;
    }
    #ifdef SVC_TASKS
    if (__atomic_load_n(&s->hop, __ATOMIC_ACQUIRE) == HOP_POSTED) {
        s->hop_fn(s->hop_arg);
        __atomic_store_n(&s->hop, (uint8_t)HOP_DONE, __ATOMIC_RELEASE);
        wake(s);
    }
    #endif
    if (__atomic_load_n(&s->req, __ATOMIC_ACQUIRE) != REQ_POSTED) {
        return MOYCORE_SVC_BAD;
    }
    *name = s->req_name;
    *args = s->req_args;
    *n = s->req_n;
    return MOYCORE_SVC_OK;
}

int moycore_svc_answer(uint32_t h, const char *ans, size_t n) {
    svc_t *s = row_of(h);
    if (s == NULL) {
        return MOYCORE_SVC_STALE;
    }
    if (__atomic_load_n(&s->req, __ATOMIC_ACQUIRE) != REQ_POSTED) {
        return MOYCORE_SVC_BAD;
    }
    s->ans = ans != NULL ? mem_copy(ans, n) : NULL;
    s->ans_n = n;
    __atomic_store_n(&s->req, (uint8_t)REQ_ANSWERED, __ATOMIC_RELEASE);
    wake(s);
    return MOYCORE_SVC_OK;
}

int moycore_svc_info(uint32_t h, moycore_svc_info_t *out) {
    svc_t *s = row_of(h);
    if (s == NULL) {
        return MOYCORE_SVC_STALE;
    }
    out->state = s->state;
    out->strikes = s->strikes;
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
        w = (uint8_t)(w + (__atomic_load_n(&s->on[i].id, __ATOMIC_ACQUIRE) != 0));
    }
    out->wakes = w;
    out->mail = s->mail_n;
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
    s_row[(h & 0xFFu) - 1u] = NULL;
    free(s);
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
                end(s, MOYCORE_SVC_EXIT_KILLED);
            }
            free(s);
            s_row[i] = NULL;
        }
    }
    #endif
}

#endif // SVC_NONE

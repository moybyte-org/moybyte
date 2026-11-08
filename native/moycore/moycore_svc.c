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

#ifdef SVC_NONE

// The S3s: no user service in Lua, and no table in their internal RAM.
int moycore_svc_start(uint32_t pid, const char *id, const char *src, size_t n, uint32_t *h) {
    (void)pid;
    (void)id;
    (void)src;
    (void)n;
    (void)h;
    return MOYCORE_SVC_BAD;
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

enum { CALL_OK, CALL_ERR, CALL_ABORT, CALL_EXIT };

typedef struct {
    uint32_t id;                // 0: a free timer
    uint32_t due;               // ms
    uint32_t every;             // 0: once
    int ref;                    // the callback, in the registry
} svc_timer_t;

typedef struct {
    lua_State *L;
    char *src;
    size_t n;
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
    // the call under way
    int64_t t0;
    uint32_t budget;
    uint8_t aborted;
    uint8_t exiting;
    int32_t exit_req;
    char id[32];
    char why[MOYCORE_SVC_WHY];
#ifdef SVC_TASKS
    TaskHandle_t task;
#endif
} svc_t;

static svc_t *s_row[MOYCORE_SVCS];
static uint16_t s_gen;

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

// The function on top of the main state's stack, called on a fresh coroutine
// within `budget` microseconds.
static int call(svc_t *s, uint32_t budget) {
    lua_State *L = s->L;
    lua_State *co = lua_newthread(L);
    lua_rotate(L, -2, 1);
    lua_xmove(L, co, 1);
    lua_sethook(co, hook, LUA_MASKCOUNT, HOOK_COUNT);
    s->aborted = 0;
    s->t0 = now_us();
    s->budget = budget;
    int nres = 0;
    int rc = lua_resume(co, L, 0, &nres);
    s->cpu_us += (uint32_t)(now_us() - s->t0);
    int out;
    if (s->exiting) {
        out = CALL_EXIT;
    } else if (s->aborted) {
        out = CALL_ABORT;
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
    lua_settop(L, 0);
    return out;
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

static void timer_end(svc_t *s, svc_timer_t *t) {
    if (s->L != NULL) {
        luaL_unref(s->L, LUA_REGISTRYINDEX, t->ref);
    }
    t->id = 0;
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
    if (++s->next_id == 0) {
        s->next_id = 1;
    }
    t->id = s->next_id;
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

static int l_cancel(lua_State *L) {
    svc_t *s = of_state(L);
    svc_timer_t *t = timer_of(s, (uint32_t)luaL_checkinteger(L, 1));
    if (t != NULL) {
        timer_end(s, t);
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
    lua_pushnumber(L, (lua_Number)(of_state(L)->cpu_us + (uint32_t)(now_us() - of_state(L)->t0))
                      / 1e6f);
    return 1;
}

static const luaL_Reg MOYBYTE[] = {
    { "every", l_every }, { "after", l_after }, { "cancel", l_cancel },
    { "log", l_log }, { "exit", l_exit }, { NULL, NULL },
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
    lua_setglobal(L, "moybyte");
}

// -- the life ------------------------------------------------------------------------

static void end(svc_t *s, int32_t code) {
    if (s->L != NULL) {
        lua_close(s->L);
        s->L = NULL;
    }
    for (uint32_t i = 0; i < MOYCORE_SVC_TIMERS; i++) {
        s->tm[i].id = 0;
    }
    free(s->src);
    s->src = NULL;
    s->exit = code;
    s->state = MOYCORE_SVC_EXITED;
}

static bool has_timers(const svc_t *s) {
    for (uint32_t i = 0; i < MOYCORE_SVC_TIMERS; i++) {
        if (s->tm[i].id != 0) {
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

static void start(svc_t *s) {
    s->L = lua_newstate(svc_alloc, s);
    if (s->L == NULL) {
        snprintf(s->why, sizeof s->why, "no memory for its state");
        end(s, MOYCORE_SVC_EXIT_CRASHED);
        return;
    }
    *(svc_t **)lua_getextraspace(s->L) = s;
    open_names(s->L);
    if (luaL_loadbufferx(s->L, s->src, s->n, s->id, "t") != LUA_OK) {
        const char *m = lua_tostring(s->L, -1);
        snprintf(s->why, sizeof s->why, "%s", m != NULL ? m : "it does not load");
        end(s, MOYCORE_SVC_EXIT_CRASHED);
        return;
    }
    free(s->src);
    s->src = NULL;
    if (ended_by(s, call(s, MOYCORE_SVC_STOP_MS * 1000u), "its start")) {
        return;
    }
    if (lua_getglobal(s->L, "moy_start") == LUA_TFUNCTION) {
        if (ended_by(s, call(s, MOYCORE_SVC_STOP_MS * 1000u), "its start")) {
            return;
        }
    } else {
        lua_settop(s->L, 0);
    }
    if (!has_timers(s)) {
        end(s, 0);
        return;
    }
    s->state = MOYCORE_SVC_RUNNING;
}

static void stop(svc_t *s) {
    s->state = MOYCORE_SVC_STOPPING;
    if (lua_getglobal(s->L, "moy_stop") == LUA_TFUNCTION) {
        if (ended_by(s, call(s, MOYCORE_SVC_STOP_MS * 1000u), "its stop")) {
            return;
        }
    }
    end(s, 0);
}

// What is due on `s` at `now`; the ms to its next deadline.
static uint32_t run(svc_t *s, uint32_t now) {
    s->now = now;
    if (s->state == MOYCORE_SVC_STARTING) {
        start(s);
    }
    if (s->state == MOYCORE_SVC_RUNNING && s->stop_req) {
        stop(s);
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
        int rc = call(s, MOYCORE_SVC_ABORT_US);
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
    if (s->state != MOYCORE_SVC_RUNNING) {
        return UINT32_MAX;
    }
    if (s->stop_req) {
        return 0;
    }
    if (!has_timers(s)) {
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

int moycore_svc_start(uint32_t pid, const char *id, const char *src, size_t n, uint32_t *h) {
    uint32_t slot = 0;
    while (slot < MOYCORE_SVCS && s_row[slot] != NULL) {
        slot++;
    }
    if (slot == MOYCORE_SVCS) {
        return MOYCORE_SVC_FULL;
    }
    svc_t *s = mem_calloc(sizeof(svc_t));
    char *copy = malloc(n + 1u);
    if (s == NULL || copy == NULL) {
        free(s);
        free(copy);
        return MOYCORE_SVC_NOMEM;
    }
    memcpy(copy, src, n);
    copy[n] = 0;
    s->src = copy;
    s->n = n;
    s->pid = pid;
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
    #ifdef SVC_TASKS
    if (!s->done) {
        xTaskNotifyGive(s->task);
    }
    #endif
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

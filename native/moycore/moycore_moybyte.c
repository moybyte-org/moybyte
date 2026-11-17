// moycore_moybyte: one process's `moybyte` table. moycore_moybyte.h has the
// contract.

#include "moy_platform.h"

#include "moycore_moybyte.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lauxlib.h"
#include "lua.h"
#include "moy_evq.h"
#include "moy_json.h"
#include "moy_kmem.h"
#include "moy_super.h"

// The console calls, generated from native/os_calls.json (none in an image
// without the app ABI).
#if defined(__has_include) && __has_include("moy_app_lua_rows.h")
#include "moy_app_lua_rows.h"
#else
#define MOY_APP_LUA_CALLS(X)
#endif

#include "moy_log.h"
#pragma weak moy_log_write
enum { LOG_ERR = 3, LOG_WARNING = 4, LOG_INFO = 6 };

// The JSON a message's arguments and a console call's cross as
// (native/moy_app/moy_app_lua.h), where the image has the app ABI.
#if __has_include("moy_app_lua.h")
#include "moy_app_lua.h"
#else
int moy_app_lua_json_push(lua_State *L, const char *text, size_t n);
const char *moy_app_lua_json_dump(lua_State *L, int idx, size_t *n);
#endif
#pragma weak moy_app_lua_json_push
#pragma weak moy_app_lua_json_dump

static const char KEY[] = "moy.mb";
static const char CALL_KEY[] = "moy.call";

moycore_mb_t *moycore_mb_of(lua_State *L) {
    lua_getfield(L, LUA_REGISTRYINDEX, KEY);
    moycore_mb_t *mb = (moycore_mb_t *)lua_touserdata(L, -1);
    lua_pop(L, 1);
    return mb;
}

static moycore_mb_t *mb_of(lua_State *L) {
    moycore_mb_t *mb = moycore_mb_of(L);
    if (mb == NULL) {
        luaL_error(L, "no moybyte table in this state");
    }
    return mb;
}

uint32_t moycore_mb_pid(const moycore_mb_t *mb) {
    return mb != NULL ? mb->pid : 0u;
}

// The registry table `ref`'s slot `id` set to the value at `idx` (nil drops it).
static void put(lua_State *L, int ref, uint32_t id, int idx) {
    idx = lua_absindex(L, idx);
    lua_rawgeti(L, LUA_REGISTRYINDEX, ref);
    lua_pushvalue(L, idx);
    lua_rawseti(L, -2, (lua_Integer)id);
    lua_pop(L, 1);
}

static void keep(lua_State *L, moycore_mb_t *mb, uint32_t id, int fn, bool once) {
    put(L, mb->fns, id, fn);
    if (once) {
        lua_pushboolean(L, 1);
        put(L, mb->once, id, -1);
        lua_pop(L, 1);
    }
}

static void drop(lua_State *L, moycore_mb_t *mb, uint32_t id) {
    lua_pushnil(L);
    put(L, mb->fns, id, -1);
    put(L, mb->once, id, -1);
    lua_pop(L, 1);
}

static int on_of(const moycore_mb_t *mb, uint32_t id) {
    for (uint32_t k = 0; id != 0u && k < MOYCORE_MB_ONS; k++) {
        if (mb->on[k].id == id) {
            return (int)k;
        }
    }
    return -1;
}

// -- the wakes -----------------------------------------------------------------------

static int timer(lua_State *L, bool every) {
    moycore_mb_t *mb = mb_of(L);
    lua_Integer ms = luaL_checkinteger(L, 1);
    luaL_checktype(L, 2, LUA_TFUNCTION);
    if (ms < (every ? 1 : 0)) {
        ms = every ? 1 : 0;
    }
    uint32_t id = 0;
    int rc = moy_evq_timer(mb->pid, (uint32_t)ms, every ? (uint32_t)ms : 0u, &id);
    if (rc != MOY_EVQ_OK) {
        return luaL_error(L, rc == MOY_EVQ_FULL ? "every timer of the process is taken"
                                                : "the process has no queue");
    }
    keep(L, mb, id, 2, !every);
    lua_pushinteger(L, (lua_Integer)id);
    return 1;
}

static int l_every(lua_State *L) {
    return timer(L, true);
}

static int l_after(lua_State *L) {
    return timer(L, false);
}

// An alarm at wall time `t` (moy_evq_at, under the queue's `alarms`): a
// fraction rounds up, no earlier than `t` (Lua's numbers are floats).
static int l_at(lua_State *L) {
    moycore_mb_t *mb = mb_of(L);
    int64_t whole;
    if (lua_isinteger(L, 1)) {
        whole = (int64_t)lua_tointeger(L, 1);
    } else {
        lua_Number t = luaL_checknumber(L, 1);
        whole = t >= 4.0e9f ? (int64_t)4e9 : t <= -4.0e9f ? (int64_t)-4e9 : (int64_t)t;
        if ((lua_Number)whole < t) {
            whole++;
        }
    }
    luaL_checktype(L, 2, LUA_TFUNCTION);
    uint32_t id = 0;
    int rc = moy_evq_at(mb->pid, whole, &id);
    if (rc != MOY_EVQ_OK) {
        return luaL_error(L, rc == MOY_EVQ_DENIED ? "moybyte.at needs \"alarms\""
                             : rc == MOY_EVQ_FULL ? "every timer of the process is taken"
                                                  : "the process has no queue");
    }
    keep(L, mb, id, 2, true);
    lua_pushinteger(L, (lua_Integer)id);
    return 1;
}

static int l_on(lua_State *L) {
    moycore_mb_t *mb = mb_of(L);
    size_t n;
    const char *name = luaL_checklstring(L, 1, &n);
    luaL_checktype(L, 2, LUA_TFUNCTION);
    if (n == 0 || n >= MOY_EVQ_NAME) {
        return luaL_error(L, "no message %s", name);
    }
    int k = on_of(mb, 0u);
    for (uint32_t i = 0; k < 0 && i < MOYCORE_MB_ONS; i++) {
        if (mb->on[i].id == 0u) {
            k = (int)i;
        }
    }
    if (k < 0) {
        return luaL_error(L, "every message wake of the process is taken");
    }
    if (moy_evq_listen(mb->pid, name, true) != MOY_EVQ_OK) {
        return luaL_error(L, "the process listens on too many names");
    }
    uint32_t id = moy_evq_id();
    memcpy(mb->on[k].name, name, n + 1u);
    mb->on[k].id = id;
    keep(L, mb, id, 2, false);
    lua_pushinteger(L, (lua_Integer)id);
    return 1;
}

static int l_cancel(lua_State *L) {
    moycore_mb_t *mb = mb_of(L);
    uint32_t id = (uint32_t)luaL_checkinteger(L, 1);
    lua_rawgeti(L, LUA_REGISTRYINDEX, mb->fns);
    bool held = id != 0u && lua_rawgeti(L, -1, (lua_Integer)id) != LUA_TNIL;
    lua_pop(L, 2);
    if (held) {
        int k = on_of(mb, id);
        if (k >= 0) {
            moy_evq_listen(mb->pid, mb->on[k].name, false);
            mb->on[k].id = 0u;
        } else {
            moy_evq_timer_cancel(mb->pid, id);
        }
        drop(L, mb, id);
    }
    lua_pushboolean(L, held);
    return 1;
}

static int l_log(lua_State *L) {
    moycore_mb_t *mb = mb_of(L);
    lua_Integer level = luaL_checkinteger(L, 1);
    size_t n;
    const char *text = luaL_tolstring(L, 2, &n);
    if (moy_log_write != NULL) {
        moy_log_write(level < 0 ? 0 : level > 7 ? 7 : (int)level, mb->pid, text, n);
    }
    return 0;
}

// The values from `first` on as one JSON array, pushed.
static void push_array(lua_State *L, int first, int last) {
    int n = last >= first ? last - first + 1 : 0;
    luaL_checkstack(L, 2 * n + 4, NULL);
    lua_pushliteral(L, "[");
    for (int i = first; i <= last; i++) {
        if (i > first) {
            lua_pushliteral(L, ", ");
        }
        moy_app_lua_json_dump(L, i, NULL);
    }
    lua_pushliteral(L, "]");
    lua_concat(L, n > 0 ? 2 * n + 1 : 2);
}

static int l_send(lua_State *L) {
    moycore_mb_t *mb = mb_of(L);
    const char *app = luaL_checkstring(L, 1);
    const char *name = luaL_checkstring(L, 2);
    if (moy_app_lua_json_dump == NULL) {
        return luaL_error(L, "no messages in this build");
    }
    push_array(L, 3, lua_gettop(L));
    size_t len;
    const char *text = lua_tolstring(L, -1, &len);
    uint32_t took = 0;
    int rc = moy_evq_send(mb->pid, app, name, text, len, &took, NULL);
    if (rc == MOY_EVQ_OK) {
        lua_pushinteger(L, took);
        return 1;
    }
    lua_pushnil(L);
    lua_pushstring(L, rc == MOY_EVQ_FULL ? "full" : "no queue");
    return 2;
}

int moycore_mb_call(lua_State *L) {
    int n = lua_gettop(L);
    while (n > 0 && lua_isnil(L, n)) {
        n--;                    // a call's trailing nils are not its arguments
    }
    lua_settop(L, n);
    if (moy_app_lua_json_dump == NULL || moy_app_lua_json_push == NULL) {
        return luaL_error(L, "no console calls in this build");
    }
    if (lua_getfield(L, LUA_REGISTRYINDEX, CALL_KEY) != LUA_TFUNCTION) {
        return luaL_error(L, "no console calls in this run");
    }
    const char *verb = lua_tostring(L, lua_upvalueindex(1));
    lua_pushstring(L, verb + sizeof "moybyte." - 1u);
    push_array(L, 1, n);
    lua_call(L, 2, 1);
    return 1;
}

static const luaL_Reg OWN[] = {
    { "every", l_every }, { "after", l_after }, { "at", l_at }, { "on", l_on },
    { "cancel", l_cancel }, { "log", l_log }, { "send", l_send }, { NULL, NULL },
};

void moycore_mb_open(lua_State *L, moycore_mb_t *mb, uint32_t pid, lua_CFunction console) {
    memset(mb, 0, sizeof *mb);
    mb->pid = pid;
    lua_pushlightuserdata(L, mb);
    lua_setfield(L, LUA_REGISTRYINDEX, KEY);
    lua_newtable(L);
    mb->fns = luaL_ref(L, LUA_REGISTRYINDEX);
    lua_newtable(L);
    mb->once = luaL_ref(L, LUA_REGISTRYINDEX);
    if (console == moycore_mb_call && lua_getglobal(L, "__moybyte_call") == LUA_TFUNCTION) {
        lua_setfield(L, LUA_REGISTRYINDEX, CALL_KEY);
        lua_pushnil(L);
        lua_setglobal(L, "__moybyte_call");
    } else if (console == moycore_mb_call) {
        lua_pop(L, 1);
    }
    if (lua_getglobal(L, "moybyte") != LUA_TTABLE) {
        lua_pop(L, 1);
        lua_newtable(L);
    }
    luaL_setfuncs(L, OWN, 0);
    lua_pushinteger(L, LOG_ERR);
    lua_setfield(L, -2, "ERR");
    lua_pushinteger(L, LOG_WARNING);
    lua_setfield(L, -2, "WARNING");
    lua_pushinteger(L, LOG_INFO);
    lua_setfield(L, -2, "INFO");
#define CALL(name)                          \
    lua_pushliteral(L, "moybyte." name);    \
    lua_pushcclosure(L, console, 1);        \
    lua_setfield(L, -2, name);
    MOY_APP_LUA_CALLS(CALL)
#undef CALL
    lua_setglobal(L, "moybyte");
}

uint32_t moycore_mb_count(const moycore_mb_t *mb) {
    if (mb == NULL) {
        return 0u;
    }
    uint32_t n = moy_evq_timers(mb->pid);
    for (uint32_t k = 0; k < MOYCORE_MB_ONS; k++) {
        n += mb->on[k].id != 0u;
    }
    return n;
}

bool moycore_mb_wakes(const moycore_mb_t *mb) {
    return moycore_mb_count(mb) != 0u;
}

// A message's sender, then its JSON array's values, each in its place, a
// null as nil: how many pushed.
static int push_values(lua_State *L, const char *msg, size_t n);

static int push_args(lua_State *L, const moy_evq_ev_t *ev, const char *msg, size_t n) {
    luaL_checkstack(L, 1, NULL);
    lua_pushstring(L, ev->app);
    return 1 + push_values(L, msg, n);
}

// The values of JSON array `msg` (n bytes) pushed, each in its place, a null
// as nil: how many.
static int push_values(lua_State *L, const char *msg, size_t n) {
    int got = 0;
    if (msg == NULL || moy_app_lua_json_push == NULL) {
        return got;
    }
    const char *end = msg + n;
    const char *v = moy_json_ws(msg, end);
    const char *past = moy_json_value(v, end, 0u);
    if (past != NULL && moy_json_kind(v, past) == MOY_JSON_ARR) {
        moy_json_iter_t it;
        const char *k, *ke, *e, *ee;
        moy_json_iter(&it, v, past);
        while (moy_json_next(&it, &k, &ke, &e, &ee)) {
            luaL_checkstack(L, 1, NULL);
            moy_app_lua_json_push(L, e, (size_t)(ee - e));
            got++;
        }
    }
    return got;
}

int moycore_mb_global(lua_State *L, const char *fn, const char *json, size_t n, char *err,
                      size_t errn) {
    if (lua_getglobal(L, fn) != LUA_TFUNCTION) {
        lua_pop(L, 1);
        return 0;
    }
    int nargs = push_values(L, json, n);
    if (lua_pcall(L, nargs, 0, 0) != LUA_OK) {
        const char *m = lua_tostring(L, -1);
        snprintf(err, errn, "%s", m != NULL ? m : "an error object");
        lua_pop(L, 1);
        return -1;
    }
    return 0;
}

int moycore_mb_next(lua_State *L, moycore_mb_t *mb, const moy_evq_ev_t *ev, const char *msg,
                    size_t n, uint32_t *at) {
    if (ev->from == 0u && (strcmp(ev->name, MOY_EVQ_TIMER) == 0
                           || strcmp(ev->name, MOY_EVQ_AT) == 0)) {
        if (*at != 0u || msg == NULL || n < 2u || msg[0] != '[') {
            return -1;
        }
        *at = MOYCORE_MB_ONS;
        uint32_t id = (uint32_t)strtoul(msg + 1, NULL, 10);
        lua_rawgeti(L, LUA_REGISTRYINDEX, mb->fns);
        if (lua_rawgeti(L, -1, (lua_Integer)id) != LUA_TFUNCTION) {
            lua_pop(L, 2);
            return -1;          // cancelled since it went off
        }
        lua_remove(L, -2);
        lua_rawgeti(L, LUA_REGISTRYINDEX, mb->once);
        bool once = lua_rawgeti(L, -1, (lua_Integer)id) != LUA_TNIL;
        lua_pop(L, 2);
        if (once) {
            drop(L, mb, id);
        }
        return 0;
    }
    for (uint32_t k = *at; k < MOYCORE_MB_ONS; k++) {
        if (mb->on[k].id != 0u && strcmp(mb->on[k].name, ev->name) == 0) {
            *at = k + 1u;
            lua_rawgeti(L, LUA_REGISTRYINDEX, mb->fns);
            lua_rawgeti(L, -1, (lua_Integer)mb->on[k].id);
            lua_remove(L, -2);
            return push_args(L, ev, msg, n);
        }
    }
    *at = MOYCORE_MB_ONS;
    return -1;
}

void moycore_mb_end(lua_State *L, moycore_mb_t *mb) {
    if (mb == NULL) {
        return;
    }
    for (uint32_t k = 0; k < MOYCORE_MB_ONS; k++) {
        if (mb->on[k].id != 0u) {
            moy_evq_listen(mb->pid, mb->on[k].name, false);
            mb->on[k].id = 0u;
        }
    }
    lua_rawgeti(L, LUA_REGISTRYINDEX, mb->fns);
    lua_pushnil(L);
    while (lua_next(L, -2) != 0) {
        lua_pop(L, 1);
        moy_evq_timer_cancel(mb->pid, (uint32_t)lua_tointeger(L, -1));
    }
    lua_pop(L, 1);
}

int moycore_mb_serve(lua_State *L, moycore_mb_t *mb, char *err, size_t errn) {
    if (mb == NULL || moy_super_held(mb->pid) || moy_evq_queued(mb->pid) == 0u) {
        return 0;
    }
    moy_evq_step(mb->pid);              // MOY_EVQ_BATCH a serve at most
    int ran = 0;
    for (;;) {
        char small[256];
        char *big = NULL;
        const char *msg = small;
        moy_evq_ev_t ev;
        int rc = moy_evq_take(mb->pid, &ev, small, sizeof small);
        if (rc == MOY_EVQ_RANGE) {
            big = (char *)moy_kalloc(ev.n + 1u, MOY_KMEM_PSRAM);
            if (big == NULL || moy_evq_take(mb->pid, &ev, big, ev.n + 1u) != MOY_EVQ_OK) {
                moy_kfree(big);
                return ran;
            }
            msg = big;
        } else if (rc != MOY_EVQ_OK) {
            return ran;
        }
        uint32_t at = 0;
        int nargs;
        while ((nargs = moycore_mb_next(L, mb, &ev, msg, ev.n, &at)) >= 0) {
            if (lua_pcall(L, nargs, 0, 0) != LUA_OK) {
                const char *m = lua_tostring(L, -1);
                snprintf(err, errn, "%s", m != NULL ? m : "an error object");
                lua_pop(L, 1);
                moy_kfree(big);
                return -1;
            }
            ran++;
        }
        moy_kfree(big);
    }
}

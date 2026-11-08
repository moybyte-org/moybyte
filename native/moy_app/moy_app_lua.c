// The app ABI's Lua face (moy_app_lua.h has the contract). It compiles to
// nothing in a build without the Lua VM's headers.

#if defined(__has_include)
#if __has_include("lua.h")
#define MOY_APP_LUA 1
#endif
#endif

#if MOY_APP_LUA

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "lauxlib.h"
#include "lua.h"

#include "moy_app.h"
#include "moy_app_lua.h"
#include "moy_app_lua_rows.h"
#include "moy_json.h"

// The run this state's names serve, its files kind's name, the hop its calls
// take to the VM's task (NULL: they run in place) and the errno of its last
// row: the state's own, a userdata its registry holds under RUN_KEY's address.
typedef struct {
    moy_app_run_t run;
    char kind[16];
    moy_app_lua_hop_fn hop;
    int why;
} lrun_t;

static const char RUN_KEY = 0;

static lrun_t *lrun(lua_State *L) {
    lua_rawgetp(L, LUA_REGISTRYINDEX, &RUN_KEY);
    lrun_t *u = lua_touserdata(L, -1);
    lua_pop(L, 1);
    return u;
}

// The errno of the state's run's last IO answer.
static int run_why(lua_State *L) {
    lrun_t *u = lrun(L);
    return u != NULL ? u->why : EIO;
}

// fn(arg) on the VM's task: through `hop` where the state has one, else here.
// 0, or -1 where the hop found nobody to serve it (fn not run).
static int on_vm(lua_State *L, moy_app_lua_hop_fn hop, void (*fn)(void *), void *arg) {
    if (hop == NULL) {
        fn(arg);
        return 0;
    }
    return hop(L, fn, arg);
}

// -- a growing byte buffer held in a userdata at a fixed stack slot ------------------

typedef struct {
    lua_State *L;
    int idx;
    uint8_t *p;
    size_t n, cap;
} buf_t;

static void buf_init(lua_State *L, buf_t *b, size_t cap) {
    b->L = L;
    b->p = lua_newuserdatauv(L, cap, 0);
    b->idx = lua_gettop(L);
    b->n = 0;
    b->cap = cap;
}

// Room for `n` more bytes: the buffer moved to a userdata twice as large (or
// more) in its slot when it has none.
static void buf_reserve(buf_t *b, size_t n) {
    if (n <= b->cap - b->n) {
        return;
    }
    size_t c = b->cap * 2u;
    while (c - b->n < n) {
        c *= 2u;
    }
    uint8_t *q = lua_newuserdatauv(b->L, c, 0);
    memcpy(q, b->p, b->n);
    lua_replace(b->L, b->idx);
    b->p = q;
    b->cap = c;
}

static void buf_put(buf_t *b, const void *d, size_t n) {
    buf_reserve(b, n);
    if (n) {
        memcpy(b->p + b->n, d, n);
    }
    b->n += n;
}

static void buf_str(buf_t *b, const char *s) {
    buf_put(b, s, strlen(s));
}

// A field of the door's arguments: its little-endian length, then its bytes.
static void buf_field(buf_t *b, const void *d, size_t n) {
    uint8_t q[4] = { (uint8_t)n, (uint8_t)(n >> 8), (uint8_t)(n >> 16), (uint8_t)(n >> 24) };
    buf_put(b, q, 4);
    buf_put(b, d, n);
}

static void field_arg(lua_State *L, buf_t *b, int i) {
    size_t n;
    const char *s = luaL_checklstring(L, i, &n);
    buf_field(b, s, n);
}

static void field_opt(lua_State *L, buf_t *b, int i) {
    size_t n = 0;
    const char *s = lua_isnoneornil(L, i) ? "" : luaL_checklstring(L, i, &n);
    buf_field(b, s, n);
}

static void field_kind(lua_State *L, buf_t *b) {
    lrun_t *u = lrun(L);
    const char *k = u != NULL ? u->kind : "";
    buf_field(b, k, strlen(k));
}

// -- calling a row ---------------------------------------------------------------------

// Row `k` with the packed arguments in `b`: its answer (>= 0, the bytes left on
// the stack's top as a userdata, `*ans` pointing at them) or a code negated. An
// answer that does not fit is asked again into a buffer eight times the size.
// A state with no run, or a run whose state is gone, answers STALE, nil
// pushed; a grant that ended answers STALE from the row.
typedef struct {
    const moy_app_run_t *run;
    uint32_t row;
    const uint8_t *arg;
    size_t n;
    uint8_t *ans;
    size_t cap;
    int32_t r;
    int why;
} role_t;

// On the VM's task: the row called through the ROLE door, its errno kept.
static void role_run(void *p) {
    role_t *c = p;
    if (!moy_app_run_live(c->run)) {
        c->r = -MOY_APP_STALE;
        c->why = EIO;
        return;
    }
    c->r = moy_app_role(c->run->a, c->run->g, c->row, c->arg, c->n, c->ans, c->cap);
    c->why = moy_app_why(c->run->a);
}

static int32_t row_call(lua_State *L, int k, const buf_t *b, const uint8_t **ans) {
    lrun_t *u = lrun(L);
    int row = k;
    if (u == NULL || row < 0) {
        lua_pushnil(L);
        *ans = NULL;
        return -MOY_APP_STALE;
    }
    size_t cap = 1024u;
    for (;;) {
        uint8_t *p = lua_newuserdatauv(L, cap, 0);
        role_t c = { &u->run, (uint32_t)row, b != NULL ? b->p : NULL, b != NULL ? b->n : 0u,
                     p, cap, -MOY_APP_NEEDS_VM, EIO };
        on_vm(L, u->hop, role_run, &c);
        u->why = c.why;
        if (c.r != -MOY_APP_FULL || cap >= (64u << 20)) {
            *ans = p;
            return c.r;
        }
        lua_pop(L, 1);
        cap *= 8u;
    }
}

// A grant problem is an error, as the Python face raises it.
static int raise_rc(lua_State *L, int rc) {
    switch (rc) {
        case MOY_APP_STALE:
            return luaL_error(L, "a grant that ended");
        case MOY_APP_DENIED:
            return luaL_error(L, "a role the grant does not hold");
        case MOY_APP_NOMEM:
            return luaL_error(L, "not enough memory");
        case MOY_APP_FULL:
            return luaL_error(L, "the answer is too long");
        case MOY_APP_NEEDS_VM:
            return luaL_error(L, "no console to serve it");
        default:
            return luaL_error(L, "an argument the role refuses");
    }
}

// The `err` of a storage row's code, as the Python face's: the text pushed.
static void push_err(lua_State *L, int rc) {
    switch (rc) {
        case MOY_APP_NOSTORE:
            lua_pushliteral(L, "NO STORAGE");
            return;
        case MOY_APP_IO: {
            int e = run_why(L);
            lua_pushfstring(L, "[Errno %d] %s", e, strerror(e));
            return;
        }
        case MOY_APP_BAD:
            lua_pushliteral(L, "a refused argument");
            return;
        case MOY_APP_NOMEM:
            lua_pushliteral(L, "memory allocation failed");
            return;
        default:
            raise_rc(L, rc);
    }
}

// -- files ------------------------------------------------------------------------------

// (value, err): a name or a blob answered, nil and the err, or nil, nil where
// the row found nothing (`absent_nil`).
static int ret_text(lua_State *L, int32_t r, const uint8_t *ans, int absent_nil) {
    if (r >= 0) {
        lua_pushlstring(L, (const char *)ans, (size_t)r);
        lua_pushnil(L);
        return 2;
    }
    lua_pushnil(L);
    if (absent_nil && -r == MOY_APP_ABSENT) {
        lua_pushnil(L);
    } else {
        push_err(L, -r);
    }
    return 2;
}

static int l_files_ready(lua_State *L) {
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_FILES_READY, NULL, &ans);
    if (r < 0) {
        return raise_rc(L, -r);
    }
    lua_pushboolean(L, r != 0);
    return 1;
}

static int l_files_list(lua_State *L) {
    buf_t b;
    buf_init(L, &b, 64u);
    field_kind(L, &b);
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_FILES_LIST, &b, &ans);
    if (r < 0) {
        return ret_text(L, r, ans, 0);
    }
    lua_newtable(L);
    lua_Integer i = 0;
    for (int32_t at = 0; at < r;) {
        const uint8_t *z = memchr(ans + at, 0, (size_t)(r - at));
        size_t n = z != NULL ? (size_t)(z - (ans + at)) : (size_t)(r - at);
        lua_pushlstring(L, (const char *)ans + at, n);
        lua_rawseti(L, -2, ++i);
        at += (int32_t)n + 1;
    }
    lua_pushnil(L);
    return 2;
}

static int named(lua_State *L, int k, int nargs, int absent_nil) {
    lua_settop(L, nargs);
    buf_t b;
    buf_init(L, &b, 128u);
    field_kind(L, &b);
    for (int i = 1; i <= nargs; i++) {
        field_arg(L, &b, i);
    }
    const uint8_t *ans;
    int32_t r = row_call(L, k, &b, &ans);
    return ret_text(L, r, ans, absent_nil);
}

static int l_files_load(lua_State *L) {
    return named(L, MOY_T_FILES_LOAD, 1, 1);
}

static int l_files_save(lua_State *L) {
    return named(L, MOY_T_FILES_SAVE, 2, 0);
}

static int l_files_delete(lua_State *L) {
    return named(L, MOY_T_FILES_DELETE, 1, 0);
}

static int l_files_duplicate(lua_State *L) {
    return named(L, MOY_T_FILES_DUPLICATE, 1, 0);
}

static int l_files_rename(lua_State *L) {
    return named(L, MOY_T_FILES_RENAME, 2, 0);
}

static int l_files_new_name(lua_State *L) {
    lua_settop(L, 1);
    buf_t b;
    buf_init(L, &b, 128u);
    field_kind(L, &b);
    field_opt(L, &b, 1);
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_FILES_NEW_NAME, &b, &ans);
    return ret_text(L, r, ans, 0);
}

// A document is its own text: ("", err) where it cannot be read, "" for none.
static int l_files_load_text(lua_State *L) {
    named(L, MOY_T_FILES_LOAD, 1, 1);
    if (lua_isnil(L, -2)) {
        lua_pushliteral(L, "");
        lua_replace(L, -3);
    }
    return 2;
}

// -- prefs: each value JSON in its settings row ----------------------------------------

#define JSON_DEPTH 32

static void json_put(lua_State *L, buf_t *b, int i, int depth);

static void json_quote(buf_t *b, const char *s, size_t n) {
    size_t need = moy_json_quote(s, n, NULL, 0);
    buf_reserve(b, need);
    moy_json_quote(s, n, (char *)b->p + b->n, need);
    b->n += need;
}

static void json_number(lua_State *L, buf_t *b, int i) {
    char t[48];
    if (lua_isinteger(L, i)) {
        snprintf(t, sizeof t, LUA_INTEGER_FMT, (LUAI_UACINT)lua_tointeger(L, i));
    } else {
        double v = (double)lua_tonumber(L, i);
        if (isnan(v)) {
            strcpy(t, "NaN");
        } else if (isinf(v)) {
            strcpy(t, v > 0 ? "Infinity" : "-Infinity");
        } else {
            snprintf(t, sizeof t, LUA_NUMBER_FMT, (LUAI_UACNUMBER)v);
            if (strpbrk(t, ".eEn") == NULL) {
                strcat(t, ".0");
            }
        }
    }
    buf_str(b, t);
}

// The table at `i` as json.dumps writes it: a sequence (the keys exactly
// 1..n) as an array, any other as an object with its keys sorted, a number
// key as its text; the empty table an array.
typedef struct {
    const char *s;
    lua_Integer at;             // where its key is kept in the anchor table
} jkey_t;

static int key_cmp(const void *x, const void *y) {
    return strcmp(((const jkey_t *)x)->s, ((const jkey_t *)y)->s);
}

static void json_table(lua_State *L, buf_t *b, int i, int depth) {
    if (depth >= JSON_DEPTH) {
        luaL_error(L, "a value nested too deep");
    }
    lua_Integer n = (lua_Integer)lua_rawlen(L, i);
    lua_Integer keys = 0;
    int seq = 1;
    lua_pushnil(L);
    while (lua_next(L, i) != 0) {
        lua_pop(L, 1);
        keys++;
        if (!lua_isinteger(L, -1) || lua_tointeger(L, -1) < 1 || lua_tointeger(L, -1) > n) {
            seq = 0;
        }
    }
    if (seq) {
        buf_put(b, "[", 1);
        for (lua_Integer k = 1; k <= n; k++) {
            if (k > 1) {
                buf_put(b, ", ", 2);
            }
            lua_rawgeti(L, i, k);
            json_put(L, b, lua_gettop(L), depth + 1);
            lua_pop(L, 1);
        }
        buf_put(b, "]", 1);
        return;
    }
    // Each key kept at 2k - 1 of an anchor table, its text at 2k, while the
    // texts are sorted.
    lua_createtable(L, (int)(2 * keys), 0);
    int kt = lua_gettop(L);
    jkey_t *ks = lua_newuserdatauv(L, (size_t)keys * sizeof *ks, 0);
    lua_Integer m = 0;
    lua_pushnil(L);
    while (lua_next(L, i) != 0) {
        lua_pop(L, 1);
        int t = lua_type(L, -1);
        if (t != LUA_TSTRING && t != LUA_TNUMBER) {
            luaL_error(L, "a key that is not a string or a number");
        }
        lua_pushvalue(L, -1);
        lua_rawseti(L, kt, 2 * m + 1);
        lua_pushvalue(L, -1);
        ks[m].s = lua_tostring(L, -1);
        ks[m].at = 2 * m + 1;
        lua_rawseti(L, kt, 2 * m + 2);
        m++;
    }
    qsort(ks, (size_t)m, sizeof *ks, key_cmp);
    buf_put(b, "{", 1);
    for (lua_Integer k = 0; k < m; k++) {
        if (k) {
            buf_put(b, ", ", 2);
        }
        json_quote(b, ks[k].s, strlen(ks[k].s));
        buf_put(b, ": ", 2);
        lua_rawgeti(L, kt, ks[k].at);
        lua_rawget(L, i);
        json_put(L, b, lua_gettop(L), depth + 1);
        lua_pop(L, 1);
    }
    buf_put(b, "}", 1);
    lua_pop(L, 2);
}

static void json_put(lua_State *L, buf_t *b, int i, int depth) {
    switch (lua_type(L, i)) {
        case LUA_TNIL:
        case LUA_TNONE:
            buf_str(b, "null");
            return;
        case LUA_TBOOLEAN:
            buf_str(b, lua_toboolean(L, i) ? "true" : "false");
            return;
        case LUA_TNUMBER:
            json_number(L, b, i);
            return;
        case LUA_TSTRING: {
            size_t n;
            const char *s = lua_tolstring(L, i, &n);
            json_quote(b, s, n);
            return;
        }
        case LUA_TTABLE:
            json_table(L, b, i, depth);
            return;
        default:
            luaL_error(L, "a %s is not JSON", luaL_typename(L, i));
    }
}

static void json_push(lua_State *L, const char *v, const char *end);

static void json_push_string(lua_State *L, const char *s, const char *end) {
    luaL_Buffer lb;
    size_t n = moy_json_strlen(s, end);
    char *out = luaL_buffinitsize(L, &lb, n);
    luaL_pushresultsize(&lb, moy_json_str(s, end, out));
}

static void json_push(lua_State *L, const char *v, const char *end) {
    luaL_checkstack(L, 4, "a value nested too deep");
    switch (moy_json_kind(v, end)) {
        case MOY_JSON_STR:
            json_push_string(L, v, end);
            return;
        case MOY_JSON_INT:
        case MOY_JSON_FLOAT: {
            int64_t x;
            if (moy_json_kind(v, end) == MOY_JSON_INT && moy_json_int(v, end, &x) == 1
                && x >= (int64_t)LUA_MININTEGER && x <= (int64_t)LUA_MAXINTEGER) {
                lua_pushinteger(L, (lua_Integer)x);
                return;
            }
            char t[64];
            size_t n = (size_t)(end - v) < sizeof t - 1u ? (size_t)(end - v) : sizeof t - 1u;
            memcpy(t, v, n);
            t[n] = 0;
            lua_pushnumber(L, (lua_Number)strtod(t, NULL));
            return;
        }
        case MOY_JSON_TRUE:
            lua_pushboolean(L, 1);
            return;
        case MOY_JSON_FALSE:
            lua_pushboolean(L, 0);
            return;
        case MOY_JSON_ARR:
        case MOY_JSON_OBJ: {
            moy_json_iter_t it;
            const char *k, *ke, *e, *ee;
            lua_Integer i = 0;
            lua_newtable(L);
            moy_json_iter(&it, v, end);
            while (moy_json_next(&it, &k, &ke, &e, &ee)) {
                if (k != NULL) {
                    json_push_string(L, k, ke);
                    json_push(L, e, ee);
                    lua_rawset(L, -3);
                } else {
                    json_push(L, e, ee);
                    lua_rawseti(L, -2, ++i);
                }
            }
            return;
        }
        default:
            lua_pushnil(L);
    }
}

int moy_app_lua_json_push(lua_State *L, const char *text, size_t n) {
    const char *end = text + n;
    const char *v = moy_json_ws(text, end);
    const char *past = moy_json_value(v, end, 0u);
    if (past == NULL || moy_json_ws(past, end) != end) {
        return 0;
    }
    json_push(L, v, past);
    return 1;
}

const char *moy_app_lua_json_dump(lua_State *L, int idx, size_t *n) {
    idx = lua_absindex(L, idx);
    buf_t j;
    buf_init(L, &j, 128u);
    json_put(L, &j, idx, 0);
    lua_pushlstring(L, (const char *)j.p, j.n);
    lua_remove(L, j.idx);
    return lua_tolstring(L, -1, n);
}

static int l_prefs_get(lua_State *L) {
    lua_settop(L, 2);
    buf_t b;
    buf_init(L, &b, 128u);
    field_arg(L, &b, 1);
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_PREFS_GET, &b, &ans);
    if (r == -MOY_APP_ABSENT) {
        lua_pushvalue(L, 2);
        return 1;
    }
    if (r < 0) {
        return raise_rc(L, -r);
    }
    const char *v = (const char *)ans, *end = v + r;
    v = moy_json_ws(v, end);
    const char *past = moy_json_value(v, end, 0u);
    if (past == NULL) {
        return luaL_error(L, "a setting that is not JSON");
    }
    json_push(L, v, past);
    return 1;
}

static int l_prefs_set(lua_State *L) {
    luaL_checkany(L, 1);
    lua_settop(L, 2);
    buf_t j;
    buf_init(L, &j, 128u);
    json_put(L, &j, 2, 0);
    buf_t b;
    buf_init(L, &b, j.n + 128u);
    field_arg(L, &b, 1);
    buf_field(&b, j.p, j.n);
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_PREFS_SET, &b, &ans);
    if (r < 0 && r != -MOY_APP_ABSENT) {
        return raise_rc(L, -r);
    }
    lua_pushnil(L);
    return 1;
}

static int l_prefs_clear(lua_State *L) {
    lua_settop(L, 1);
    buf_t b;
    buf_init(L, &b, 128u);
    field_arg(L, &b, 1);
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_PREFS_CLEAR, &b, &ans);
    if (r < 0 && r != -MOY_APP_ABSENT) {
        return raise_rc(L, -r);
    }
    lua_pushnil(L);
    return 1;
}

// -- the shell's rows -------------------------------------------------------------------

static int l_set_theme(lua_State *L) {
    lua_settop(L, 2);
    buf_t b;
    buf_init(L, &b, 64u);
    field_arg(L, &b, 1);
    field_opt(L, &b, 2);
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_THEME_SET, &b, &ans);
    if (r < 0) {
        return raise_rc(L, -r);
    }
    lua_pushnil(L);
    return 1;
}

static int l_open_app(lua_State *L) {
    lua_settop(L, 1);
    buf_t b;
    buf_init(L, &b, 64u);
    field_arg(L, &b, 1);
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_NAV_OPEN_APP, &b, &ans);
    if (r < 0) {
        return raise_rc(L, -r);
    }
    lua_pushboolean(L, r > 0);
    return 1;
}

// -- io and os over the grant's kind ---------------------------------------------------

#define FILE_MT "moybyte.file"

typedef struct {
    char *p;                    // the item's bytes while open (malloc), else NULL
    size_t n, cap, pos;
    uint8_t rd, wr, app, dirty, closed;
    char name[MOY_UF_NAME_MAX + 1u];
} file_t;

// A path is a name of the grant's kind ("./" before it allowed): one that
// names a folder, climbs or is absolute names nothing this process may reach.
static const char *path_name(const char *p) {
    if (p[0] == '.' && p[1] == '/') {
        p += 2;
    }
    if (*p == 0 || strcmp(p, ".") == 0 || strcmp(p, "..") == 0
        || strchr(p, '/') != NULL || strchr(p, '\\') != NULL
        || strlen(p) > MOY_UF_NAME_MAX) {
        return NULL;
    }
    return p;
}

// The failure Lua's io answers: nil, "<path>: <why>", errno.
static int fail(lua_State *L, const char *path, int e, const char *why) {
    lua_pushnil(L);
    lua_pushfstring(L, "%s: %s", path, why != NULL ? why : strerror(e));
    lua_pushinteger(L, e);
    return 3;
}

// A row's code as io's failure: nothing there ENOENT, the store's own errno
// as it failed, a refused name EINVAL, and no store the Python face's err.
static int fail_rc(lua_State *L, const char *path, int rc) {
    switch (rc) {
        case MOY_APP_ABSENT:
            return fail(L, path, ENOENT, NULL);
        case MOY_APP_IO:
            return fail(L, path, run_why(L), NULL);
        case MOY_APP_BAD:
            return fail(L, path, EINVAL, NULL);
        default:
            push_err(L, rc);
            return fail(L, path, EIO, lua_tostring(L, -1));
    }
}

static int file_reserve(file_t *f, size_t need) {
    if (need <= f->cap) {
        return 1;
    }
    size_t c = f->cap ? f->cap : 256u;
    while (c < need) {
        c *= 2u;
    }
    char *q = realloc(f->p, c);
    if (q == NULL) {
        return 0;
    }
    f->p = q;
    f->cap = c;
    return 1;
}

// Save the item whole through the files role: 0, or the row's code.
static int file_save(lua_State *L, file_t *f) {
    if (!f->dirty) {
        return 0;
    }
    buf_t b;
    buf_init(L, &b, f->n + 128u);
    field_kind(L, &b);
    buf_field(&b, f->name, strlen(f->name));
    buf_field(&b, f->p, f->n);
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_FILES_SAVE, &b, &ans);
    lua_pop(L, 2);
    if (r < 0) {
        return -r;
    }
    f->dirty = 0;
    return 0;
}

static file_t *file_check(lua_State *L) {
    file_t *f = luaL_checkudata(L, 1, FILE_MT);
    if (f->closed) {
        luaL_error(L, "attempt to use a closed file");
    }
    return f;
}

static int l_io_open(lua_State *L) {
    const char *path = luaL_checkstring(L, 1);
    const char *mode = luaL_optstring(L, 2, "r");
    const char *m = mode;
    if (*m == 0 || strchr("rwa", *m) == NULL) {
        return luaL_argerror(L, 2, "invalid mode");
    }
    m++;
    int plus = *m == '+';
    m += plus;
    while (*m == 'b') {
        m++;
    }
    if (*m != 0) {
        return luaL_argerror(L, 2, "invalid mode");
    }
    const char *name = path_name(path);
    if (name == NULL) {
        return fail(L, path, EACCES, NULL);
    }
    int has = 0;
    const uint8_t *ans = NULL;
    int32_t r = 0;
    if (mode[0] != 'w') {
        buf_t b;
        buf_init(L, &b, 128u);
        field_kind(L, &b);
        buf_field(&b, name, strlen(name));
        r = row_call(L, MOY_T_FILES_LOAD, &b, &ans);
        if (r >= 0) {
            has = 1;
        } else if (!(mode[0] == 'a' && -r == MOY_APP_ABSENT)) {
            return fail_rc(L, path, -r);
        }
    }
    file_t *f = lua_newuserdatauv(L, sizeof *f, 0);
    memset(f, 0, sizeof *f);
    luaL_setmetatable(L, FILE_MT);
    strcpy(f->name, name);
    f->rd = mode[0] == 'r' || plus;
    f->wr = mode[0] != 'r' || plus;
    f->app = mode[0] == 'a';
    f->dirty = mode[0] == 'w';
    if (has && r > 0) {
        if (!file_reserve(f, (size_t)r)) {
            return luaL_error(L, "not enough memory");
        }
        memcpy(f->p, ans, (size_t)r);
        f->n = (size_t)r;
    }
    f->pos = f->app ? f->n : 0u;
    return 1;
}

static int l_io_type(lua_State *L) {
    luaL_checkany(L, 1);
    file_t *f = luaL_testudata(L, 1, FILE_MT);
    if (f == NULL) {
        luaL_pushfail(L);
    } else {
        lua_pushstring(L, f->closed ? "closed file" : "file");
    }
    return 1;
}

static int file_close(lua_State *L) {
    file_t *f = file_check(L);
    int rc = file_save(L, f);
    free(f->p);
    f->p = NULL;
    f->closed = 1;
    if (rc != 0) {
        return fail_rc(L, f->name, rc);
    }
    lua_pushboolean(L, 1);
    return 1;
}

static int file_gc(lua_State *L) {
    file_t *f = luaL_checkudata(L, 1, FILE_MT);
    if (!f->closed) {
        file_save(L, f);
        free(f->p);
        f->p = NULL;
        f->closed = 1;
    }
    return 0;
}

static int file_tostring(lua_State *L) {
    file_t *f = luaL_checkudata(L, 1, FILE_MT);
    if (f->closed) {
        lua_pushliteral(L, "file (closed)");
    } else {
        lua_pushfstring(L, "file (%p)", (void *)f);
    }
    return 1;
}

static int file_flush(lua_State *L) {
    file_t *f = file_check(L);
    int rc = file_save(L, f);
    if (rc != 0) {
        return fail_rc(L, f->name, rc);
    }
    lua_settop(L, 1);
    return 1;
}

static int file_write(lua_State *L) {
    file_t *f = file_check(L);
    if (!f->wr) {
        return fail(L, f->name, EBADF, NULL);
    }
    int top = lua_gettop(L);
    for (int i = 2; i <= top; i++) {
        size_t n;
        const char *s;
        if (lua_type(L, i) == LUA_TNUMBER) {
            s = luaL_tolstring(L, i, &n);
        } else {
            s = luaL_checklstring(L, i, &n);
            lua_pushnil(L);
        }
        if (f->app) {
            f->pos = f->n;
        }
        if (!file_reserve(f, f->pos + n)) {
            return luaL_error(L, "not enough memory");
        }
        memcpy(f->p + f->pos, s, n);
        f->pos += n;
        if (f->pos > f->n) {
            f->n = f->pos;
        }
        f->dirty = 1;
        lua_pop(L, 1);
    }
    lua_settop(L, 1);
    return 1;
}

// One read format at `f`'s position: 1 with the value pushed, or 0 with fail.
static int read_one(lua_State *L, file_t *f, int arg) {
    size_t left = f->n - f->pos;
    const char *at = f->p != NULL ? f->p + f->pos : "";
    if (lua_type(L, arg) == LUA_TNUMBER) {
        size_t k = (size_t)luaL_checkinteger(L, arg);
        if (left == 0) {
            luaL_pushfail(L);
            return 0;
        }
        k = k < left ? k : left;
        lua_pushlstring(L, at, k);
        f->pos += k;
        return 1;
    }
    const char *fmt = luaL_checkstring(L, arg);
    if (*fmt == '*') {
        fmt++;
    }
    switch (*fmt) {
        case 'a':
            lua_pushlstring(L, at, left);
            f->pos = f->n;
            return 1;
        case 'l':
        case 'L': {
            if (left == 0) {
                luaL_pushfail(L);
                return 0;
            }
            const char *nl = memchr(at, '\n', left);
            size_t k = nl != NULL ? (size_t)(nl - at) : left;
            lua_pushlstring(L, at, k + (*fmt == 'L' && nl != NULL ? 1u : 0u));
            f->pos += k + (nl != NULL ? 1u : 0u);
            return 1;
        }
        case 'n': {
            size_t i = 0;
            while (i < left && strchr(" \t\r\n\f\v", at[i]) != NULL) {
                i++;
            }
            char t[201];
            size_t k = 0;
            while (i + k < left && k < sizeof t - 1u
                   && strchr("0123456789+-.eExXaAbBcCdDfFpP", at[i + k]) != NULL) {
                t[k] = at[i + k];
                k++;
            }
            t[k] = 0;
            f->pos += i + k;
            if (k == 0 || lua_stringtonumber(L, t) == 0) {
                luaL_pushfail(L);
                return 0;
            }
            return 1;
        }
        default:
            return luaL_argerror(L, arg, "invalid format");
    }
}

static int file_read_from(lua_State *L, file_t *f, int first) {
    int top = lua_gettop(L);
    if (!f->rd) {
        return fail(L, f->name, EBADF, NULL);
    }
    if (top < first) {
        lua_pushliteral(L, "l");
        top = first;
    }
    int n = 0;
    for (int i = first; i <= top; i++) {
        n++;
        if (!read_one(L, f, i)) {
            break;
        }
    }
    return n;
}

static int file_read(lua_State *L) {
    return file_read_from(L, file_check(L), 2);
}

static int file_seek(lua_State *L) {
    static const char *const whence[] = { "set", "cur", "end", NULL };
    file_t *f = file_check(L);
    int w = luaL_checkoption(L, 2, "cur", whence);
    lua_Integer off = luaL_optinteger(L, 3, 0);
    lua_Integer base = w == 0 ? 0 : w == 1 ? (lua_Integer)f->pos : (lua_Integer)f->n;
    if (base + off < 0) {
        return fail(L, f->name, EINVAL, NULL);
    }
    f->pos = (size_t)(base + off);
    if (f->pos > f->n) {
        if (!file_reserve(f, f->pos)) {
            return luaL_error(L, "not enough memory");
        }
        memset(f->p + f->n, 0, f->pos - f->n);
        f->n = f->pos;
    }
    lua_pushinteger(L, (lua_Integer)f->pos);
    return 1;
}

// The iterator: upvalue 1 the file, 2 whether it closes at the end, then the
// formats.
static int lines_next(lua_State *L) {
    file_t *f = lua_touserdata(L, lua_upvalueindex(1));
    if (f->closed) {
        return luaL_error(L, "file is already closed");
    }
    int nf = (int)lua_tointeger(L, lua_upvalueindex(3));
    lua_settop(L, 0);
    for (int i = 0; i < nf; i++) {
        lua_pushvalue(L, lua_upvalueindex(4 + i));
    }
    int n = file_read_from(L, f, 1);
    if (lua_toboolean(L, -n)) {
        return n;
    }
    if (lua_toboolean(L, lua_upvalueindex(2))) {
        lua_settop(L, 0);
        lua_pushvalue(L, lua_upvalueindex(1));
        file_close(L);
    }
    return 0;
}

static int push_lines(lua_State *L, int close) {
    int n = lua_gettop(L) - 1;
    luaL_argcheck(L, n <= 250, 252, "too many arguments");
    lua_pushvalue(L, 1);
    lua_pushboolean(L, close);
    lua_pushinteger(L, n);
    lua_rotate(L, 2, 3);
    lua_pushcclosure(L, lines_next, 3 + n);
    return 1;
}

static int file_lines(lua_State *L) {
    file_check(L);
    return push_lines(L, 0);
}

static int l_io_lines(lua_State *L) {
    const char *path = luaL_checkstring(L, 1);
    lua_pushcfunction(L, l_io_open);
    lua_pushvalue(L, 1);
    lua_pushliteral(L, "r");
    lua_call(L, 2, 3);
    if (lua_isnil(L, -3)) {
        return luaL_error(L, "%s", lua_tostring(L, -2) != NULL ? lua_tostring(L, -2) : path);
    }
    lua_pop(L, 2);
    lua_replace(L, 1);
    push_lines(L, 1);
    lua_pushnil(L);
    lua_pushnil(L);
    lua_pushvalue(L, 1);
    return 4;
}

static int l_io_close(lua_State *L) {
    luaL_checkudata(L, 1, FILE_MT);
    lua_settop(L, 1);
    return file_close(L);
}

static int l_os_remove(lua_State *L) {
    const char *path = luaL_checkstring(L, 1);
    const char *name = path_name(path);
    if (name == NULL) {
        return fail(L, path, EACCES, NULL);
    }
    buf_t b;
    buf_init(L, &b, 128u);
    field_kind(L, &b);
    buf_field(&b, name, strlen(name));
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_FILES_DELETE, &b, &ans);
    if (r < 0) {
        return fail_rc(L, path, -r);
    }
    lua_pushboolean(L, 1);
    return 1;
}

static int l_os_rename(lua_State *L) {
    const char *from = luaL_checkstring(L, 1);
    const char *to = luaL_checkstring(L, 2);
    const char *a = path_name(from), *z = path_name(to);
    if (a == NULL || z == NULL) {
        return fail(L, a == NULL ? from : to, EACCES, NULL);
    }
    buf_t b;
    buf_init(L, &b, 128u);
    field_kind(L, &b);
    buf_field(&b, a, strlen(a));
    buf_field(&b, z, strlen(z));
    const uint8_t *ans;
    int32_t r = row_call(L, MOY_T_FILES_RENAME, &b, &ans);
    if (r < 0) {
        return fail_rc(L, from, -r);
    }
    lua_pushboolean(L, 1);
    return 1;
}

static int field_of(lua_State *L, const char *key, int d) {
    int t = lua_getfield(L, 1, key);
    lua_Integer v = d;
    if (t != LUA_TNIL) {
        v = lua_tointeger(L, -1);
    } else if (d < 0) {
        return luaL_error(L, "field '%s' missing in date table", key);
    }
    lua_pop(L, 1);
    return (int)v;
}

static int l_os_time(lua_State *L) {
    time_t t;
    if (lua_isnoneornil(L, 1)) {
        t = time(NULL);
    } else {
        luaL_checktype(L, 1, LUA_TTABLE);
        struct tm ts;
        memset(&ts, 0, sizeof ts);
        ts.tm_year = field_of(L, "year", -1) - 1900;
        ts.tm_mon = field_of(L, "month", -1) - 1;
        ts.tm_mday = field_of(L, "day", -1);
        ts.tm_hour = field_of(L, "hour", 12);
        ts.tm_min = field_of(L, "min", 0);
        ts.tm_sec = field_of(L, "sec", 0);
        ts.tm_isdst = -1;
        t = mktime(&ts);
    }
    if (t == (time_t)-1) {
        luaL_pushfail(L);
        return 1;
    }
    lua_pushinteger(L, (lua_Integer)t);
    return 1;
}

static int l_os_clock(lua_State *L) {
    lua_pushnumber(L, (lua_Number)clock() / (lua_Number)CLOCKS_PER_SEC);
    return 1;
}

static int l_os_difftime(lua_State *L) {
    lua_Number a = (lua_Number)luaL_checkinteger(L, 1);
    lua_Number b = (lua_Number)luaL_optinteger(L, 2, 0);
    lua_pushnumber(L, a - b);
    return 1;
}

// -- opening ----------------------------------------------------------------------------

// What the open reads of the run, on the VM's task: whether its state is
// made, and its grant's files kind's name.
typedef struct {
    const moy_app_run_t *run;
    int live;
    char kind[16];
} open_t;

static void open_read(void *p) {
    open_t *o = p;
    o->live = moy_app_run_live(o->run);
    const moy_grant_t *gr;
    if (o->live && moy_app_grant_get(o->run->a, o->run->g, &gr) == MOY_APP_OK
        && gr->kind >= 0) {
        const char *k = moy_app_kind_name(gr->kind);
        if (k != NULL && strlen(k) < sizeof o->kind) {
            strcpy(o->kind, k);
        }
    }
}

int moy_app_lua_open(lua_State *L, const struct moy_app_run *run) {
    return moy_app_lua_open_hop(L, run, NULL);
}

static int l_files_save_text(lua_State *L) {
    return l_files_save(L);
}

// The names a run's role binding holds, each opened for the role it serves
// (moy_app_lua_rows.h, generated from native/os_calls.json): a global, or a
// function of a global table.
typedef struct {
    int8_t role;
    const char *tbl, *name;
    lua_CFunction fn;
} lname_t;

static const lname_t NAMES[] = {
#define NAME(role, tbl, name, fn, row) { role, tbl, name, fn },
    MOY_APP_LUA_NAMES(NAME)
#undef NAME
};

int moy_app_lua_open_hop(lua_State *L, const struct moy_app_run *run,
                         moy_app_lua_hop_fn hop) {
    open_t o = { run, 0, { 0 } };
    if (run == NULL || on_vm(L, hop, open_read, &o) != 0 || !o.live) {
        return 0;
    }
    lrun_t *u = lua_newuserdatauv(L, sizeof(lrun_t), 0);
    u->run = *run;
    memcpy(u->kind, o.kind, sizeof u->kind);
    u->hop = hop;
    u->why = 0;
    lua_rawsetp(L, LUA_REGISTRYINDEX, &RUN_KEY);
    uint32_t roles = run->roles;
    int files = (roles >> MOY_ROLE_FILES) & 1u && u->kind[0] != 0;

    lua_newtable(L);                    // os: every app run's clock
    static const luaL_Reg os_clock[] = {
        { "time", l_os_time }, { "clock", l_os_clock }, { "difftime", l_os_difftime },
        { NULL, NULL },
    };
    luaL_setfuncs(L, os_clock, 0);
    if (files) {
        static const luaL_Reg os_files[] = {
            { "remove", l_os_remove }, { "rename", l_os_rename }, { NULL, NULL },
        };
        luaL_setfuncs(L, os_files, 0);
    }
    lua_setglobal(L, "os");

    if (files) {
        static const luaL_Reg file_methods[] = {
            { "read", file_read }, { "write", file_write }, { "lines", file_lines },
            { "close", file_close }, { "flush", file_flush }, { "seek", file_seek },
            { NULL, NULL },
        };
        static const luaL_Reg file_meta[] = {
            { "__gc", file_gc }, { "__close", file_gc }, { "__tostring", file_tostring },
            { NULL, NULL },
        };
        luaL_newmetatable(L, FILE_MT);
        luaL_setfuncs(L, file_meta, 0);
        lua_newtable(L);
        luaL_setfuncs(L, file_methods, 0);
        lua_setfield(L, -2, "__index");
        lua_pop(L, 1);

        static const luaL_Reg io_fns[] = {
            { "open", l_io_open }, { "lines", l_io_lines }, { "close", l_io_close },
            { "type", l_io_type }, { NULL, NULL },
        };
        lua_newtable(L);
        luaL_setfuncs(L, io_fns, 0);
        lua_setglobal(L, "io");
    }
    for (size_t i = 0; i < sizeof NAMES / sizeof NAMES[0]; i++) {
        const lname_t *nm = &NAMES[i];
        if (!((roles >> nm->role) & 1u) || (nm->role == MOY_ROLE_FILES && !files)) {
            continue;
        }
        if (nm->tbl == NULL) {
            lua_pushcfunction(L, nm->fn);
            lua_setglobal(L, nm->name);
            continue;
        }
        if (lua_getglobal(L, nm->tbl) != LUA_TTABLE) {
            lua_pop(L, 1);
            lua_newtable(L);
            lua_pushvalue(L, -1);
            lua_setglobal(L, nm->tbl);
        }
        lua_pushcfunction(L, nm->fn);
        lua_setfield(L, -2, nm->name);
        lua_pop(L, 1);
    }
    return 1;
}

#endif // MOY_APP_LUA

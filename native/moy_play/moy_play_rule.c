// The VM-free rule (moy_play.h has it in words).

#include <stdio.h>
#include <string.h>

#include "moy_json.h"
#include "moy_play.h"
#include "moy_rt.h"
#include "moy_vol.h"

static const char *const NATIVE_PERMS[] = { "graphics", "input", "audio", "multiplayer" };

static const char *const WHY[MOY_PLAY_WHY_COUNT] = {
    "free", "broken", "runtime", "absent", "type", "permission", "import",
};

// native/moy_app's table, where the image carries it: a compiled app's imports
// are read through it (moy_app_wasm.h). Weak: the Zero, which runs no apps,
// and the host's moy_play library have none, and there an app keeps the VM
// (rule 2).
int moy_app_role_of(const char *perm, size_t n) __attribute__((weak));
int moy_app_wasm_imports(const uint8_t *head, size_t n,
                         int (*fn)(void *ctx, const uint8_t *mod, size_t mn,
                                   const uint8_t *name, size_t nn),
                         void *ctx) __attribute__((weak));
int moy_app_wasm_row_named(const char *name, size_t n) __attribute__((weak));
int moy_app_table_c_row(uint32_t row) __attribute__((weak));

#define APP_EXT "moybyte.app"
// The most of main.wasm read for its imports: the type and import sections
// lead the module, and a head this long that has not reached the end of the
// imports keeps the VM.
#define HEAD_MAX 4096u

const char *moy_play_why_name(uint8_t why) {
    return why < MOY_PLAY_WHY_COUNT ? WHY[why] : "?";
}

static bool is_str(moy_cat_json_t f) {
    return f.v != NULL && *f.v == '"';
}

static bool str_is(moy_cat_json_t f, const char *s) {
    return is_str(f) && moy_json_str_is(f.v, f.e, s, strlen(s));
}

static bool native_perm(const char *v, const char *e) {
    if (*v != '"') {
        return false;
    }
    for (size_t i = 0; i < sizeof(NATIVE_PERMS) / sizeof(NATIVE_PERMS[0]); i++) {
        if (moy_json_str_is(v, e, NATIVE_PERMS[i], strlen(NATIVE_PERMS[i]))) {
            return true;
        }
    }
    return false;
}

// A permission a compiled app may hold VM-free: one whose role's rows it
// imports are C-served (its imports decide, below). "files:<kind>" is files.
static bool role_perm(const char *v, const char *e) {
    char p[24];
    size_t n = moy_json_strlen(v, e);
    if (*v != '"' || n >= sizeof(p) || moy_app_role_of == NULL) {
        return false;
    }
    moy_json_str(v, e, p);
    p[n] = 0;
    const char *colon = strchr(p, ':');
    return moy_app_role_of(p, colon != NULL ? (size_t)(colon - p) : n) >= 0;
}

typedef struct {
    char *imp;
    size_t cap;
    bool kept;
} imports_t;

static int import_seen(void *ctx, const uint8_t *mod, size_t mn, const uint8_t *name,
                       size_t nn) {
    imports_t *c = ctx;
    if (mn != sizeof(APP_EXT) - 1u || memcmp(mod, APP_EXT, mn) != 0) {
        return 0;
    }
    int row = moy_app_wasm_row_named((const char *)name, nn);
    if (row >= 0 && moy_app_table_c_row((uint32_t)row) >= 0) {
        return 0;
    }
    if (c->imp != NULL && c->cap) {
        snprintf(c->imp, c->cap, "%s.%.*s", APP_EXT, (int)nn, (const char *)name);
    }
    c->kept = true;
    return 1;
}

// Rule 3 for a compiled app: every import it makes from the app extension is
// a C-served row. Its module's head is read from the cart's folder; one that
// cannot be read, or whose imports do not parse inside HEAD_MAX, keeps it.
static bool imports_free(const char *path, const moy_cat_entry_t *e, char *imp,
                         size_t cap) {
    char p[320];
    if (imp != NULL && cap) {
        imp[0] = 0;
    }
    if (e->main == NULL || snprintf(p, sizeof(p), "%s/%.*s", path, (int)e->main_n, e->main)
                               >= (int)sizeof(p)) {
        return false;
    }
    moy_vol_t v;
    const char *rest;
    moy_vol_file_t *f = NULL;
    uint8_t *head = moy_store_alloc(HEAD_MAX);
    size_t got = 0;
    if (head == NULL) {
        return false;
    }
    int gated = moy_vol_gate_enter(p);
    int r = moy_vol_at(p, &v, &rest);
    if (r == 0) {
        r = moy_vol_open(&v, rest, MOY_VOL_READ, &f);
    }
    while (r == 0 && got < HEAD_MAX) {
        size_t n = 0;
        r = moy_vol_read(f, head + got, HEAD_MAX - got, &n);
        if (n == 0) {
            break;
        }
        got += n;
    }
    if (f != NULL) {
        moy_vol_close(f);
    }
    moy_vol_gate_leave(gated);
    imports_t c = { imp, cap, false };
    bool ok = r == 0 && moy_app_wasm_imports(head, got, import_seen, &c) == 0 && !c.kept;
    moy_store_free(head, HEAD_MAX);
    return ok;
}

static uint8_t verdict(const char *path, const moy_cat_entry_t *e, char *imp, size_t cap) {
    if (e->broken != NULL) {
        return MOY_PLAY_WHY_BROKEN;
    }
    // 1: the runtime, with the store's default.
    const moy_rt_ops_t *rt;
    if (e->runtime.v == NULL) {
        if (!e->spec) {
            return MOY_PLAY_WHY_RUNTIME;            // "python"
        }
        rt = moy_rt_get("lua");
        if (rt == NULL) {
            return MOY_PLAY_WHY_NORT;
        }
    } else if (str_is(e->runtime, "lua") || str_is(e->runtime, "wasm")) {
        rt = moy_rt_get(str_is(e->runtime, "lua") ? "lua" : "wasm");
        if (rt == NULL) {
            return MOY_PLAY_WHY_NORT;
        }
    } else {
        return MOY_PLAY_WHY_RUNTIME;
    }
    if (rt->vm) {
        return MOY_PLAY_WHY_RUNTIME;
    }
    // 2: a game, with the store's default; or a compiled app, where the image
    // reads its imports.
    bool app = path != NULL && str_is(e->runtime, "wasm") && str_is(e->type, "app")
               && moy_app_wasm_imports != NULL && moy_app_wasm_row_named != NULL
               && moy_app_table_c_row != NULL;
    if (!app && (e->type.v == NULL ? !e->spec : !str_is(e->type, "game"))) {
        return MOY_PLAY_WHY_TYPE;
    }
    // 3: the native set, or none at all; for a compiled app also the
    // permissions of roles, whose rows it imports decide.
    if (e->permissions.v != NULL) {
        if (*e->permissions.v != '[') {
            return MOY_PLAY_WHY_PERM;
        }
        moy_json_iter_t it;
        const char *k, *ke, *v, *ve;
        moy_json_iter(&it, e->permissions.v, e->permissions.e);
        while (moy_json_next(&it, &k, &ke, &v, &ve)) {
            if (!native_perm(v, ve) && !(app && role_perm(v, ve))) {
                return MOY_PLAY_WHY_PERM;
            }
        }
        if (it.bad) {
            return MOY_PLAY_WHY_PERM;
        }
    }
    if (app && !imports_free(path, e, imp, cap)) {
        return MOY_PLAY_WHY_IMPORT;
    }
    return MOY_PLAY_FREE;
}

bool moy_play_is_game(const moy_cat_entry_t *e) {
    return e->broken == NULL && (e->type.v == NULL ? e->spec != 0 : str_is(e->type, "game"));
}

bool moy_play_vm_free(const moy_cat_entry_t *e, uint8_t *why) {
    return moy_play_vm_free_at(NULL, e, why, NULL, 0);
}

bool moy_play_vm_free_at(const char *path, const moy_cat_entry_t *e, uint8_t *why,
                         char *imp, size_t cap) {
    uint8_t w = verdict(path, e, imp, cap);
    if (why != NULL) {
        *why = w;
    }
    return w == MOY_PLAY_FREE;
}

int moy_play_census_line(const moy_cat_entry_t *e, char *out, size_t n) {
    return moy_play_census_line_at(NULL, e, out, n);
}

int moy_play_census_line_at(const char *path, const moy_cat_entry_t *e, char *out,
                            size_t n) {
    char rt[32] = "";
    if (e->runtime.v == NULL) {
        snprintf(rt, sizeof(rt), "%s", e->spec ? "lua" : "python");
    } else if (*e->runtime.v == '"' && moy_json_strlen(e->runtime.v, e->runtime.e) < sizeof(rt)) {
        rt[moy_json_str(e->runtime.v, e->runtime.e, rt)] = 0;
    } else {
        snprintf(rt, sizeof(rt), "?");
    }
    uint8_t why = 0;
    char imp[64];
    bool free_ = moy_play_vm_free_at(path, e, &why, imp, sizeof(imp));
    if (why == MOY_PLAY_WHY_IMPORT && imp[0]) {
        return snprintf(out, n, "%s vm import %s", rt, imp);
    }
    return snprintf(out, n, "%s %s %s", rt, free_ ? "free" : "vm", moy_play_why_name(why));
}

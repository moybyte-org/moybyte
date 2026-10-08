// The VM-free rule (moy_play.h has it in words).

#include <stdio.h>
#include <string.h>

#include "moy_json.h"
#include "moy_play.h"
#include "moy_rt.h"

static const char *const NATIVE_PERMS[] = { "graphics", "input", "audio", "multiplayer" };

static const char *const WHY[MOY_PLAY_WHY_COUNT] = {
    "free", "broken", "runtime", "absent", "type", "permission",
};

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

static uint8_t verdict(const moy_cat_entry_t *e) {
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
    // 2: a game, with the store's default.
    if (e->type.v == NULL ? !e->spec : !str_is(e->type, "game")) {
        return MOY_PLAY_WHY_TYPE;
    }
    // 3: the native set, or none at all.
    if (e->permissions.v != NULL) {
        if (*e->permissions.v != '[') {
            return MOY_PLAY_WHY_PERM;
        }
        moy_json_iter_t it;
        const char *k, *ke, *v, *ve;
        moy_json_iter(&it, e->permissions.v, e->permissions.e);
        while (moy_json_next(&it, &k, &ke, &v, &ve)) {
            if (!native_perm(v, ve)) {
                return MOY_PLAY_WHY_PERM;
            }
        }
        if (it.bad) {
            return MOY_PLAY_WHY_PERM;
        }
    }
    return MOY_PLAY_FREE;
}

bool moy_play_vm_free(const moy_cat_entry_t *e, uint8_t *why) {
    uint8_t w = verdict(e);
    if (why != NULL) {
        *why = w;
    }
    return w == MOY_PLAY_FREE;
}

int moy_play_census_line(const moy_cat_entry_t *e, char *out, size_t n) {
    char rt[32] = "";
    if (e->runtime.v == NULL) {
        snprintf(rt, sizeof(rt), "%s", e->spec ? "lua" : "python");
    } else if (*e->runtime.v == '"' && moy_json_strlen(e->runtime.v, e->runtime.e) < sizeof(rt)) {
        rt[moy_json_str(e->runtime.v, e->runtime.e, rt)] = 0;
    } else {
        snprintf(rt, sizeof(rt), "?");
    }
    uint8_t why = 0;
    bool free_ = moy_play_vm_free(e, &why);
    return snprintf(out, n, "%s %s %s", rt, free_ ? "free" : "vm", moy_play_why_name(why));
}

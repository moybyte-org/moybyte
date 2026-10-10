// The app ABI's kernel half (moy_app.h has the contract).

#include <string.h>

#include "moy_app.h"
#include "moy_route.h"

struct moy_app {
    const moy_htab_mem_t *mem;
    moy_htab_t *grants;
    moy_settings_t *prefs;
    uint32_t damage;            // MOY_DAMAGE_* bits
    uint8_t clip_kind;
    uint32_t clip_seq;
    uint32_t clip_len;
    char *clip;                 // MOY_APP_CLIP_MAX bytes
    uint32_t counts[MOY_ROW_N];
};

static const char *const ROLE_NAMES[MOY_ROLE_N] = {
    "damage", "surface", "theme", "files", "carts", "nav", "prefs", "notify",
    "wallpaper", "artwork", "clipboard", "install",
};

static const char *const ROW_NAMES[MOY_ROW_N] = {
    "damage.all", "damage.again", "prefs.get", "prefs.set", "prefs.clear",
    "clipboard.put_text", "clipboard.text", "clipboard.kind", "clipboard.seq",
};

static const char *const KIND_NAMES[MOY_APP_KINDS] = {
    "docs", "drawings", "sprites", "music",
};

// The allowlist: a role absent here is never granted.
static const struct { const char *perm; int role; } PERMS[] = {
    { "files", MOY_ROLE_FILES },
    { "prefs", MOY_ROLE_PREFS },
    { "appearance", MOY_ROLE_THEME },
    { "launch", MOY_ROLE_NAV },
    { "clipboard", MOY_ROLE_CLIPBOARD },
};
#define NPERMS (sizeof(PERMS) / sizeof(PERMS[0]))

// -- the state ---------------------------------------------------------------------

moy_appabi_t *moy_app_new(const moy_htab_mem_t *mem, moy_settings_t *prefs) {
    moy_appabi_t *a = mem->alloc(sizeof(moy_appabi_t));
    if (a == NULL) {
        return NULL;
    }
    memset(a, 0, sizeof(*a));
    a->mem = mem;
    a->prefs = prefs;
    a->grants = moy_htab_new(mem, MOY_KIND_GRANT, MOY_GRANT_SLOTS, sizeof(moy_grant_t));
    a->clip = mem->alloc(MOY_APP_CLIP_MAX);
    if (a->grants == NULL || a->clip == NULL) {
        moy_app_free(a);
        return NULL;
    }
    return a;
}

void moy_app_free(moy_appabi_t *a) {
    if (a == NULL) {
        return;
    }
    moy_htab_free(a->grants);
    if (a->clip != NULL) {
        a->mem->release(a->clip, MOY_APP_CLIP_MAX);
    }
    a->mem->release(a, sizeof(moy_appabi_t));
}

static moy_appabi_t *s_kernel;

moy_appabi_t *moy_app_kernel(const moy_htab_mem_t *mem) {
    if (s_kernel != NULL || mem == NULL) {
        return s_kernel;
    }
    const moy_spine_kernel_t *k = moy_spine_kernel(mem);
    if (k == NULL) {
        return NULL;
    }
    s_kernel = moy_app_new(mem, k->settings);
    return s_kernel;
}

uint32_t moy_app_kernel_grants(void) {
    return s_kernel != NULL ? moy_app_grants(s_kernel) : 0u;
}

void moy_app_fresh(moy_appabi_t *a) {
    for (uint32_t s = 0; s < moy_htab_slots(a->grants); s++) {
        uint32_t h = moy_htab_at(a->grants, s);
        const moy_grant_t *g;
        if (h != 0u && moy_app_grant_get(a, h, &g) == MOY_APP_OK
            && g->cls == MOY_GRANT_RUN) {
            moy_htab_release(a->grants, h);
        }
    }
    a->damage = 0u;
    a->clip_kind = MOY_CLIP_EMPTY;
    a->clip_len = 0u;
}

// -- the grants ----------------------------------------------------------------------

static moy_grant_t *grant_row(const moy_appabi_t *a, uint32_t h) {
    void *row = NULL;
    if (moy_htab_get(a->grants, h, &row) != MOY_HTAB_OK) {
        return NULL;
    }
    return row;
}

int moy_app_grant_get(const moy_appabi_t *a, uint32_t h, const moy_grant_t **g) {
    moy_grant_t *row = grant_row(a, h);
    if (row == NULL) {
        return MOY_APP_STALE;
    }
    *g = row;
    return MOY_APP_OK;
}

uint32_t moy_app_grants(const moy_appabi_t *a) {
    return moy_htab_count(a->grants);
}

int moy_app_grant(moy_appabi_t *a, const char *id, size_t n, uint8_t cls,
                  uint32_t roles, int kind, const char *ns, size_t ns_n,
                  uint32_t owner, uint32_t *h) {
    if (n == 0u || n > MOY_APP_ID_MAX || ns_n > MOY_APP_ID_MAX
        || kind < -1 || kind >= (int)MOY_APP_KINDS || cls > MOY_GRANT_RUN
        || roles >= (1u << MOY_ROLE_N)) {
        return MOY_APP_BAD;
    }
    moy_grant_t *row = NULL;
    for (uint32_t s = 0; s < moy_htab_slots(a->grants) && row == NULL; s++) {
        uint32_t at = moy_htab_at(a->grants, s);
        moy_grant_t *r = at != 0u ? grant_row(a, at) : NULL;
        if (r != NULL && r->cls == cls && r->id_len == n && memcmp(r->id, id, n) == 0) {
            row = r;
            *h = at;
        }
    }
    if (row == NULL) {
        void *fresh;
        int rc = moy_htab_add(a->grants, h, &fresh);
        if (rc != MOY_HTAB_OK) {
            return rc == MOY_HTAB_FULL ? MOY_APP_FULL : MOY_APP_NOMEM;
        }
        row = fresh;
        memcpy(row->id, id, n);
        row->id[n] = '\0';
        row->id_len = (uint8_t)n;
        row->cls = cls;
    }
    row->roles = roles;
    row->kind = (int8_t)kind;
    row->owner = owner;
    memcpy(row->ns, ns, ns_n);
    row->ns[ns_n] = '\0';
    row->ns_len = (uint8_t)ns_n;
    return MOY_APP_OK;
}

int moy_app_end(moy_appabi_t *a, uint32_t h) {
    return moy_htab_release(a->grants, h) == MOY_HTAB_OK ? MOY_APP_OK : MOY_APP_STALE;
}

// The grant `h`, when it is live and holds `role`; else NULL and `*rc` says why.
static const moy_grant_t *holding(const moy_appabi_t *a, uint32_t h, int role, int *rc) {
    const moy_grant_t *g = grant_row(a, h);
    if (g == NULL) {
        *rc = MOY_APP_STALE;
        return NULL;
    }
    if ((g->roles & (1u << role)) == 0u) {
        *rc = MOY_APP_DENIED;
        return NULL;
    }
    *rc = MOY_APP_OK;
    return g;
}

// -- the policy ------------------------------------------------------------------------

const char *moy_app_perm_of(int role) {
    for (size_t i = 0; i < NPERMS; i++) {
        if (PERMS[i].role == role) {
            return PERMS[i].perm;
        }
    }
    return "";
}

int moy_app_role_of(const char *perm, size_t n) {
    for (size_t i = 0; i < NPERMS; i++) {
        if (strlen(PERMS[i].perm) == n && memcmp(PERMS[i].perm, perm, n) == 0) {
            return PERMS[i].role;
        }
    }
    return -1;
}

const char *moy_app_kind_name(int kind) {
    return kind >= 0 && kind < (int)MOY_APP_KINDS ? KIND_NAMES[kind] : NULL;
}

int moy_app_kind_of(const char *name, size_t n) {
    for (int k = 0; k < (int)MOY_APP_KINDS; k++) {
        if (strlen(KIND_NAMES[k]) == n && memcmp(KIND_NAMES[k], name, n) == 0) {
            return k;
        }
    }
    return -1;
}

void moy_app_policy_init(moy_app_policy_t *p) {
    memset(p, 0, sizeof(*p));
}

void moy_app_policy_add(moy_app_policy_t *p, const char *perm, size_t n) {
    const char *colon = memchr(perm, ':', n);
    size_t head = colon != NULL ? (size_t)(colon - perm) : n;
    int role = moy_app_role_of(perm, head);
    if (role < 0) {
        return;
    }
    if (role == MOY_ROLE_FILES) {
        // A scoped grant names its kind; an unknown kind narrows to nothing,
        // never widens to the default.
        int kind = colon == NULL ? MOY_APP_DEFAULT_KIND
                                 : moy_app_kind_of(colon + 1, n - head - 1u);
        if (kind < 0) {
            return;
        }
        for (uint8_t i = 0; i < p->nkinds; i++) {
            if (p->kinds[i] == kind) {
                return;
            }
        }
        p->kinds[p->nkinds++] = (int8_t)kind;
        return;
    }
    p->roles |= 1u << role;
}

uint32_t moy_app_policy_roles(const moy_app_policy_t *p, int *kind) {
    if (p->nkinds == 1u) {
        *kind = p->kinds[0];
        return p->roles | (1u << MOY_ROLE_FILES);
    }
    *kind = -1;
    return p->roles;
}

static size_t put(char *out, size_t cap, size_t at, const char *s) {
    while (*s != '\0') {
        if (at + 1u < cap) {
            out[at] = *s;
        }
        at++;
        s++;
    }
    return at;
}

size_t moy_app_policy_error(const moy_app_policy_t *p, char *out, size_t cap) {
    out[0] = '\0';
    if (p->nkinds < 2u) {
        return 0u;
    }
    char num[4] = { (char)('0' + p->nkinds), '\0' };
    size_t at = put(out, cap, 0u, "manifest asks for ");
    at = put(out, cap, at, num);
    at = put(out, cap, at, " file kinds (");
    for (uint8_t i = 0; i < p->nkinds; i++) {
        at = put(out, cap, at, i ? ", " : "");
        at = put(out, cap, at, KIND_NAMES[p->kinds[i]]);
    }
    at = put(out, cap, at, ") - pick one");
    out[at < cap ? at : cap - 1u] = '\0';
    return at < cap ? at : cap - 1u;
}

size_t moy_app_id_for(const char *id, size_t id_n, const char *title,
                      size_t title_n, char *out, size_t cap) {
    size_t at = 0;
    if (id != NULL && id_n > 0u) {
        for (size_t i = 0; i < id_n; i++) {
            if (at + 1u < cap) {
                out[at++] = id[i];
            }
        }
    } else {
        if (title == NULL || title_n == 0u) {
            title = "app";
            title_n = 3u;
        }
        for (size_t i = 0; i < title_n && at + 1u < cap; i++) {
            char c = title[i];
            if (c >= 'A' && c <= 'Z') {
                out[at++] = (char)(c - 'A' + 'a');
            } else if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) {
                out[at++] = c;
            } else if (c == ' ' || c == '-' || c == '_') {
                out[at++] = '_';
            }
        }
        if (at == 0u) {
            at = put(out, cap, 0u, "cart");
            at = at < cap ? at : cap - 1u;
        }
    }
    out[at] = '\0';
    return at;
}

// -- damage ---------------------------------------------------------------------------

int moy_app_damage_all(moy_appabi_t *a, uint32_t g) {
    int rc;
    a->counts[MOY_ROW_DAMAGE_ALL]++;
    if (holding(a, g, MOY_ROLE_DAMAGE, &rc) != NULL) {
        a->damage |= MOY_DAMAGE_ALL;
    }
    return rc;
}

int moy_app_damage_again(moy_appabi_t *a, uint32_t g) {
    int rc;
    a->counts[MOY_ROW_DAMAGE_AGAIN]++;
    if (holding(a, g, MOY_ROLE_DAMAGE, &rc) != NULL) {
        a->damage |= MOY_DAMAGE_AGAIN;
    }
    return rc;
}

uint32_t moy_app_damage_take(moy_appabi_t *a) {
    uint32_t d = a->damage;
    a->damage = 0u;
    return d;
}

void moy_app_damage_drop(moy_appabi_t *a) {
    a->damage &= ~(uint32_t)MOY_DAMAGE_ALL;
}

// -- prefs ------------------------------------------------------------------------------

// `<ns>_<key>` into `out`: its length, or 0 for a key that is empty or too long.
static size_t pref_key(const moy_grant_t *g, const char *key, size_t n,
                       char out[MOY_APP_KEY_MAX + 1u]) {
    if (n == 0u || (size_t)g->ns_len + 1u + n > MOY_APP_KEY_MAX) {
        return 0u;
    }
    memcpy(out, g->ns, g->ns_len);
    out[g->ns_len] = '_';
    memcpy(out + g->ns_len + 1u, key, n);
    return (size_t)g->ns_len + 1u + n;
}

static int prefs_read(moy_appabi_t *a, uint32_t g, const char *key, size_t n,
                      char *out, size_t cap, size_t *len) {
    int rc;
    const moy_grant_t *row = holding(a, g, MOY_ROLE_PREFS, &rc);
    if (row == NULL) {
        return rc;
    }
    char k[MOY_APP_KEY_MAX + 1u];
    size_t kn = pref_key(row, key, n, k);
    const char *j;
    size_t jn;
    if (kn == 0u) {
        return MOY_APP_BAD;
    }
    if (a->prefs == NULL || !moy_settings_get(a->prefs, k, kn, &j, &jn)) {
        return MOY_APP_ABSENT;
    }
    if (out != NULL) {
        memcpy(out, j, jn < cap ? jn : cap);
    }
    *len = jn;
    return MOY_APP_OK;
}

// A size query that finds the row is not a call: the read after it is.
int moy_app_prefs_get(moy_appabi_t *a, uint32_t g, const char *key, size_t n,
                      char *out, size_t cap, size_t *len) {
    int rc = prefs_read(a, g, key, n, out, cap, len);
    a->counts[MOY_ROW_PREFS_GET] += out != NULL || rc != MOY_APP_OK;
    return rc;
}

int moy_app_prefs_set(moy_appabi_t *a, uint32_t g, const char *key, size_t n,
                      const char *json, size_t json_n) {
    int rc;
    a->counts[MOY_ROW_PREFS_SET]++;
    const moy_grant_t *row = holding(a, g, MOY_ROLE_PREFS, &rc);
    if (row == NULL) {
        return rc;
    }
    char k[MOY_APP_KEY_MAX + 1u];
    size_t kn = pref_key(row, key, n, k);
    if (kn == 0u || moy_settings_validate(json, json_n) != MOY_SETTINGS_OK) {
        return MOY_APP_BAD;
    }
    if (a->prefs == NULL) {
        return MOY_APP_ABSENT;
    }
    rc = moy_settings_set(a->prefs, k, kn, json, json_n);
    if (rc != MOY_SETTINGS_OK) {
        return rc == MOY_SETTINGS_NOMEM ? MOY_APP_NOMEM : MOY_APP_BAD;
    }
    moy_settings_flush(a->prefs);
    return MOY_APP_OK;
}

int moy_app_prefs_clear(moy_appabi_t *a, uint32_t g, const char *key, size_t n) {
    int rc;
    a->counts[MOY_ROW_PREFS_CLEAR]++;
    const moy_grant_t *row = holding(a, g, MOY_ROLE_PREFS, &rc);
    if (row == NULL) {
        return rc;
    }
    char k[MOY_APP_KEY_MAX + 1u];
    size_t kn = pref_key(row, key, n, k);
    if (kn == 0u) {
        return MOY_APP_BAD;
    }
    if (a->prefs == NULL || !moy_settings_delete(a->prefs, k, kn)) {
        return MOY_APP_ABSENT;
    }
    moy_settings_flush(a->prefs);
    return MOY_APP_OK;
}

// -- the clipboard ----------------------------------------------------------------------

int moy_app_clip_put_text(moy_appabi_t *a, uint32_t g, const char *text, size_t n) {
    int rc;
    a->counts[MOY_ROW_CLIPBOARD_PUT_TEXT]++;
    if (holding(a, g, MOY_ROLE_CLIPBOARD, &rc) == NULL) {
        return rc;
    }
    if (n > MOY_APP_CLIP_MAX) {
        return MOY_APP_BAD;
    }
    memcpy(a->clip, text, n);
    a->clip_len = (uint32_t)n;
    a->clip_kind = MOY_CLIP_TEXT;
    a->clip_seq++;
    return MOY_APP_OK;
}

int moy_app_clip_text(moy_appabi_t *a, uint32_t g, char *out, size_t cap, size_t *len) {
    int rc;
    if (holding(a, g, MOY_ROLE_CLIPBOARD, &rc) == NULL) {
        a->counts[MOY_ROW_CLIPBOARD_TEXT]++;
        return rc;
    }
    a->counts[MOY_ROW_CLIPBOARD_TEXT] += out != NULL;
    size_t n = a->clip_kind == MOY_CLIP_TEXT ? a->clip_len : 0u;
    if (out != NULL) {
        memcpy(out, a->clip, n < cap ? n : cap);
    }
    *len = n;
    return MOY_APP_OK;
}

int32_t moy_app_clip_kind(moy_appabi_t *a, uint32_t g) {
    int rc;
    a->counts[MOY_ROW_CLIPBOARD_KIND]++;
    if (holding(a, g, MOY_ROLE_CLIPBOARD, &rc) == NULL) {
        return -rc;
    }
    return a->clip_kind;
}

int32_t moy_app_clip_seq(moy_appabi_t *a, uint32_t g) {
    int rc;
    a->counts[MOY_ROW_CLIPBOARD_SEQ]++;
    if (holding(a, g, MOY_ROLE_CLIPBOARD, &rc) == NULL) {
        return -rc;
    }
    return (int32_t)(a->clip_seq & 0x7fffffffu);
}

// -- the counters -----------------------------------------------------------------------

uint32_t moy_app_count(const moy_appabi_t *a, int row) {
    return row >= 0 && row < MOY_ROW_N ? a->counts[row] : 0u;
}

const char *moy_app_row_name(int row) {
    return row >= 0 && row < MOY_ROW_N ? ROW_NAMES[row] : NULL;
}

const char *moy_app_role_name(int role) {
    return role >= 0 && role < MOY_ROLE_N ? ROLE_NAMES[role] : NULL;
}

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
    const moy_input_ptr_t *ptr; // the pointer the surface rows read, or NULL
    uint32_t theme_gen;         // bumped by every write of the look
    int32_t tokens[MOY_APP_TOKENS];
    uint8_t name_len, variant_len, skin_len;
    char name[MOY_APP_NAME_MAX + 1];
    char variant[MOY_APP_NAME_MAX + 1];
    char skin[MOY_APP_NAME_MAX + 1];
    const moy_uf_ops_t *uf;     // the user-files layer the store rows reach, or NULL
    uint8_t readable, writable;
    int gated;                  // the gate a store row holds now, -1 for none
    int why;                    // the errno value of the last IO answer
    uint32_t sessions;          // files sessions open
    uint32_t copy_gen;
    char root[MOY_APP_ROOT_MAX + 1];
    uint32_t counts[MOY_ROW_N];
    uint32_t seq;               // the seqlock: odd while the look or the clipboard is written
};

// The seqlock's writer half, around every write of the look and the clipboard.
static void seq_open(moy_appabi_t *a) {
    __atomic_store_n(&a->seq, a->seq + 1u, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
}

static void seq_close(moy_appabi_t *a) {
    __atomic_store_n(&a->seq, a->seq + 1u, __ATOMIC_RELEASE);
}

uint32_t moy_app_seq_begin(const moy_appabi_t *a) {
    return __atomic_load_n(&a->seq, __ATOMIC_ACQUIRE);
}

int moy_app_seq_end(const moy_appabi_t *a, uint32_t s) {
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    return __atomic_load_n(&a->seq, __ATOMIC_RELAXED) == s;
}

static const char *const ROLE_NAMES[MOY_ROLE_N] = {
    "damage", "surface", "theme", "files", "carts", "nav", "prefs", "notify",
    "wallpaper", "artwork", "clipboard", "install",
};

static const char *const ROW_NAMES[MOY_ROW_N] = {
    "damage.all", "damage.again",
    "surface.canvas", "surface.size", "surface.font_scale", "surface.chrome_scale",
    "surface.windowed", "surface.bar_h", "surface.pointer",
    "theme.colors", "theme.token", "theme.gen", "theme.light", "theme.name",
    "theme.variant", "theme.skin",
    "files.readable", "files.ready", "files.begin", "files.end", "files.list",
    "files.count", "files.load", "files.save", "files.delete", "files.duplicate",
    "files.rename", "files.new_name", "files.trash_list", "files.restore",
    "files.empty_trash", "files.history", "files.history_ops",
    "files.history_commit", "files.encode_image", "files.decode_image",
    "files.decode_cover", "files.encode_cover", "files.sig", "files.stamp",
    "files.encode_text", "files.decode_text", "files.provenance",
    "prefs.get", "prefs.set", "prefs.clear",
    "wallpaper.load_copy", "wallpaper.save_copy",
    "artwork.current", "artwork.follow",
    "clipboard.put_text", "clipboard.text", "clipboard.kind", "clipboard.seq",
};

// The token vocabulary: the base keys, the semantic roles, the bar's and the
// chrome's (docs/theming_2026-09.md section 4.1), in role-id order.
static const char *const TOKEN_NAMES[MOY_APP_TOKENS] = {
    "panel", "edge", "title", "title_ink", "accent", "hilite", "dim",
    "desktop", "desktop_pattern", "surface", "surface_alt", "ink", "ink_dim",
    "border", "selection", "selection_ink", "focus", "play", "author", "danger",
    "surface_light", "bar", "bar_edge", "bar_light", "chrome_ink",
    "chrome_ink_dim", "title_active", "title_inactive",
};
#define TOKEN_SURFACE_LIGHT 20
#define TOKEN_BAR_LIGHT 23

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
    a->gated = -1;
    for (uint32_t i = 0; i < MOY_APP_TOKENS; i++) {
        a->tokens[i] = MOY_TOKEN_ABSENT;
    }
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

void moy_app_vm_stop(void) {
    if (s_kernel != NULL) {
        s_kernel->ptr = NULL;
    }
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
    seq_open(a);
    a->clip_kind = MOY_CLIP_EMPTY;
    a->clip_len = 0u;
    seq_close(a);
    a->sessions = 0u;
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

// -- surface ----------------------------------------------------------------------------

uint32_t moy_app_surface_write(moy_appabi_t *a, const moy_app_surface_t *s) {
    uint32_t mask = 0u;
    for (uint32_t slot = 0; slot < moy_htab_slots(a->grants); slot++) {
        uint32_t at = moy_htab_at(a->grants, slot);
        moy_grant_t *r = at != 0u ? grant_row(a, at) : NULL;
        if (r != NULL && (r->roles & (1u << MOY_ROLE_SURFACE)) != 0u) {
            r->surf = *s;
            mask |= 1u << (at & 0xffu);
        }
    }
    return mask;
}

int moy_app_surface_set(moy_appabi_t *a, uint32_t g, const moy_app_surface_t *s) {
    moy_grant_t *r = grant_row(a, g);
    if (r == NULL) {
        return MOY_APP_STALE;
    }
    r->surf = *s;
    return MOY_APP_OK;
}

void moy_app_pointer_bind(moy_appabi_t *a, const moy_input_ptr_t *p) {
    a->ptr = p;
}

// The surface row of grant `g`, counted as `row`, or NULL and `*rc` says why.
static const moy_app_surface_t *surf_of(moy_appabi_t *a, uint32_t g, int row, int *rc) {
    a->counts[row]++;
    const moy_grant_t *r = holding(a, g, MOY_ROLE_SURFACE, rc);
    return r != NULL ? &r->surf : NULL;
}

int moy_app_surface_canvas(moy_appabi_t *a, uint32_t g, uint32_t *canvas) {
    int rc;
    const moy_app_surface_t *s = surf_of(a, g, MOY_ROW_SURFACE_CANVAS, &rc);
    if (s != NULL) {
        *canvas = s->canvas;
    }
    return rc;
}

int moy_app_surface_size(moy_appabi_t *a, uint32_t g, int32_t *w, int32_t *h) {
    int rc;
    const moy_app_surface_t *s = surf_of(a, g, MOY_ROW_SURFACE_SIZE, &rc);
    if (s != NULL) {
        *w = s->w;
        *h = s->h;
    }
    return rc;
}

int32_t moy_app_surface_font_scale(moy_appabi_t *a, uint32_t g) {
    int rc;
    const moy_app_surface_t *s = surf_of(a, g, MOY_ROW_SURFACE_FONT_SCALE, &rc);
    return s != NULL ? (int32_t)s->font_scale : -rc;
}

int32_t moy_app_surface_chrome_scale(moy_appabi_t *a, uint32_t g) {
    int rc;
    const moy_app_surface_t *s = surf_of(a, g, MOY_ROW_SURFACE_CHROME_SCALE, &rc);
    return s != NULL ? (int32_t)s->chrome_scale : -rc;
}

int32_t moy_app_surface_windowed(moy_appabi_t *a, uint32_t g) {
    int rc;
    const moy_app_surface_t *s = surf_of(a, g, MOY_ROW_SURFACE_WINDOWED, &rc);
    return s != NULL ? (int32_t)s->windowed : -rc;
}

int32_t moy_app_surface_bar_h(moy_appabi_t *a, uint32_t g) {
    int rc;
    const moy_app_surface_t *s = surf_of(a, g, MOY_ROW_SURFACE_BAR_H, &rc);
    return s != NULL ? s->bar_h : -rc;
}

int moy_app_surface_pointer(moy_appabi_t *a, uint32_t g, int32_t out[5]) {
    int rc;
    const moy_app_surface_t *s = surf_of(a, g, MOY_ROW_SURFACE_POINTER, &rc);
    if (s == NULL) {
        return rc;
    }
    const moy_input_ptr_t *p = a->ptr;
    if (p == NULL) {
        return MOY_APP_ABSENT;
    }
    out[0] = p->x - s->ox;
    out[1] = p->y - s->oy;
    out[2] = p->down ? 1 : 0;
    out[3] = p->click ? 1 : 0;
    out[4] = p->visible ? 1 : 0;
    return MOY_APP_OK;
}

// -- theme -------------------------------------------------------------------------------

const char *moy_app_token_name(int role) {
    return role >= 0 && role < (int)MOY_APP_TOKENS ? TOKEN_NAMES[role] : NULL;
}

int moy_app_token_flag(int role) {
    return role == TOKEN_SURFACE_LIGHT || role == TOKEN_BAR_LIGHT;
}

int moy_app_token_of(const char *name, size_t n) {
    for (int i = 0; i < (int)MOY_APP_TOKENS; i++) {
        if (strlen(TOKEN_NAMES[i]) == n && memcmp(TOKEN_NAMES[i], name, n) == 0) {
            return i;
        }
    }
    return -1;
}

static void put_name(char *dst, uint8_t *len, const char *src, size_t n) {
    memcpy(dst, src, n);
    dst[n] = '\0';
    *len = (uint8_t)n;
}

int moy_app_theme_write(moy_appabi_t *a, const char *name, size_t n,
                        const char *variant, size_t vn,
                        const int32_t tokens[MOY_APP_TOKENS]) {
    if (n > MOY_APP_NAME_MAX || vn > MOY_APP_NAME_MAX) {
        return MOY_APP_BAD;
    }
    seq_open(a);
    put_name(a->name, &a->name_len, name, n);
    put_name(a->variant, &a->variant_len, variant, vn);
    memcpy(a->tokens, tokens, sizeof(a->tokens));
    a->theme_gen++;
    seq_close(a);
    return MOY_APP_OK;
}

int moy_app_theme_write_skin(moy_appabi_t *a, const char *skin, size_t n) {
    if (n > MOY_APP_NAME_MAX) {
        return MOY_APP_BAD;
    }
    seq_open(a);
    put_name(a->skin, &a->skin_len, skin, n);
    a->theme_gen++;
    seq_close(a);
    return MOY_APP_OK;
}

uint32_t moy_app_theme_read(const moy_appabi_t *a, int32_t tokens[MOY_APP_TOKENS]) {
    memcpy(tokens, a->tokens, sizeof(a->tokens));
    return a->theme_gen;
}

int moy_app_theme_colors(moy_appabi_t *a, uint32_t g, uint32_t *gen) {
    int rc;
    a->counts[MOY_ROW_THEME_COLORS]++;
    if (holding(a, g, MOY_ROLE_THEME, &rc) != NULL) {
        *gen = a->theme_gen;
    }
    return rc;
}

int moy_app_theme_token(moy_appabi_t *a, uint32_t g, int role, int32_t *v) {
    int rc;
    a->counts[MOY_ROW_THEME_TOKEN]++;
    if (holding(a, g, MOY_ROLE_THEME, &rc) == NULL) {
        return rc;
    }
    if (role < 0 || role >= (int)MOY_APP_TOKENS) {
        return MOY_APP_BAD;
    }
    if (a->tokens[role] == MOY_TOKEN_ABSENT) {
        return MOY_APP_ABSENT;
    }
    *v = a->tokens[role];
    return MOY_APP_OK;
}

int32_t moy_app_theme_gen(moy_appabi_t *a, uint32_t g) {
    int rc;
    a->counts[MOY_ROW_THEME_GEN]++;
    if (holding(a, g, MOY_ROLE_THEME, &rc) == NULL) {
        return -rc;
    }
    return (int32_t)(a->theme_gen & 0x7fffffffu);
}

int32_t moy_app_theme_light(moy_appabi_t *a, uint32_t g) {
    int rc;
    a->counts[MOY_ROW_THEME_LIGHT]++;
    if (holding(a, g, MOY_ROLE_THEME, &rc) == NULL) {
        return -rc;
    }
    int32_t v = a->tokens[TOKEN_SURFACE_LIGHT];
    return v != MOY_TOKEN_ABSENT && v != 0 ? 1 : 0;
}

// A name row: the text and its length, counted unless it is a size query
// that succeeds.
static int name_row(moy_appabi_t *a, uint32_t g, int row, const char *text,
                    size_t n, char *out, size_t cap, size_t *len) {
    int rc;
    if (holding(a, g, MOY_ROLE_THEME, &rc) == NULL) {
        a->counts[row]++;
        return rc;
    }
    a->counts[row] += out != NULL;
    if (out != NULL) {
        memcpy(out, text, n < cap ? n : cap);
    }
    *len = n;
    return MOY_APP_OK;
}

int moy_app_theme_name(moy_appabi_t *a, uint32_t g, char *out, size_t cap, size_t *len) {
    return name_row(a, g, MOY_ROW_THEME_NAME, a->name, a->name_len, out, cap, len);
}

int moy_app_theme_variant(moy_appabi_t *a, uint32_t g, char *out, size_t cap, size_t *len) {
    return name_row(a, g, MOY_ROW_THEME_VARIANT, a->variant, a->variant_len, out, cap, len);
}

int moy_app_theme_skin(moy_appabi_t *a, uint32_t g, char *out, size_t cap, size_t *len) {
    return name_row(a, g, MOY_ROW_THEME_SKIN, a->skin, a->skin_len, out, cap, len);
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

// -- artwork ----------------------------------------------------------------------------

// Four hex digits of `s` (`n` bytes left before the closing quote), or -1.
static long hex4(const char *s, size_t n) {
    if (n < 4u) {
        return -1;
    }
    long v = 0;
    for (int k = 0; k < 4; k++) {
        char h = s[k];
        int d = h >= '0' && h <= '9' ? h - '0' : h >= 'a' && h <= 'f' ? h - 'a' + 10
                : h >= 'A' && h <= 'F' ? h - 'A' + 10 : -1;
        if (d < 0) {
            return -1;
        }
        v = (v << 4) | d;
    }
    return v;
}

// A JSON string's text decoded into `out` (UTF-8, up to cap): its length, or -1
// for text that is not one string.
static long json_str(const char *j, size_t jn, char *out, size_t cap) {
    if (jn < 2u || j[0] != '"' || j[jn - 1u] != '"') {
        return -1;
    }
    size_t n = 0;
    for (size_t i = 1; i + 1u < jn; i++) {
        uint32_t ch = (unsigned char)j[i];
        if (ch == '"') {
            return -1;
        }
        if (ch == '\\') {
            if (++i + 1u >= jn) {
                return -1;
            }
            char e = j[i];
            const char *simple = "\"\\/bfnrt", *to = "\"\\/\b\f\n\r\t";
            const char *at = strchr(simple, e);
            if (e != 'u' && (at == NULL || e == '\0')) {
                return -1;
            }
            if (e != 'u') {
                ch = (unsigned char)to[at - simple];
            } else {
                long hi = hex4(j + i + 1u, jn - i - 2u);
                if (hi < 0) {
                    return -1;
                }
                i += 4u;
                ch = (uint32_t)hi;
                if (ch >= 0xd800u && ch < 0xdc00u && i + 6u < jn
                    && j[i + 1u] == '\\' && j[i + 2u] == 'u') {
                    long lo = hex4(j + i + 3u, jn - i - 4u);
                    if (lo >= 0xdc00 && lo < 0xe000) {
                        ch = 0x10000u + ((ch - 0xd800u) << 10) + ((uint32_t)lo - 0xdc00u);
                        i += 6u;
                    }
                }
                unsigned char u[4];
                size_t un;
                if (ch < 0x80u) {
                    u[0] = (unsigned char)ch;
                    un = 1;
                } else if (ch < 0x800u) {
                    u[0] = (unsigned char)(0xc0u | (ch >> 6));
                    u[1] = (unsigned char)(0x80u | (ch & 0x3fu));
                    un = 2;
                } else if (ch < 0x10000u) {
                    u[0] = (unsigned char)(0xe0u | (ch >> 12));
                    u[1] = (unsigned char)(0x80u | ((ch >> 6) & 0x3fu));
                    u[2] = (unsigned char)(0x80u | (ch & 0x3fu));
                    un = 3;
                } else {
                    u[0] = (unsigned char)(0xf0u | (ch >> 18));
                    u[1] = (unsigned char)(0x80u | ((ch >> 12) & 0x3fu));
                    u[2] = (unsigned char)(0x80u | ((ch >> 6) & 0x3fu));
                    u[3] = (unsigned char)(0x80u | (ch & 0x3fu));
                    un = 4;
                }
                for (size_t k = 0; k < un; k++, n++) {
                    if (out != NULL && n < cap) {
                        out[n] = (char)u[k];
                    }
                }
                continue;
            }
        }
        if (out != NULL && n < cap) {
            out[n] = (char)ch;
        }
        n++;
    }
    return (long)n;
}

// One of Paint's rows, decoded: its length, or -1 when it is absent or not a string.
static long art_row(const moy_appabi_t *a, const char *key, char *out, size_t cap) {
    const char *j;
    size_t jn;
    if (a->prefs == NULL || !moy_settings_get(a->prefs, key, strlen(key), &j, &jn)) {
        return -1;
    }
    return json_str(j, jn, out, cap);
}

#define ART_KIND MOY_APP_ARTWORK_NS "_doc_kind"
#define ART_DOC MOY_APP_ARTWORK_NS "_doc"
static const char DRAWINGS[] = "drawings";

int moy_app_artwork_current(moy_appabi_t *a, uint32_t g, char *kind, size_t kcap,
                            size_t *klen, char *name, size_t ncap, size_t *nlen) {
    int rc;
    if (holding(a, g, MOY_ROLE_ARTWORK, &rc) == NULL) {
        a->counts[MOY_ROW_ARTWORK_CURRENT]++;
        return rc;
    }
    long n = art_row(a, ART_DOC, name, ncap);
    if (n < 0) {
        a->counts[MOY_ROW_ARTWORK_CURRENT]++;
        return MOY_APP_ABSENT;
    }
    a->counts[MOY_ROW_ARTWORK_CURRENT] += kind != NULL || name != NULL;
    *nlen = (size_t)n;
    long k = art_row(a, ART_KIND, kind, kcap);
    if (k < 0) {
        k = (long)(sizeof(DRAWINGS) - 1u);
        if (kind != NULL) {
            memcpy(kind, DRAWINGS, (size_t)k < kcap ? (size_t)k : kcap);
        }
    }
    *klen = (size_t)k;
    return MOY_APP_OK;
}

int moy_app_artwork_follow(moy_appabi_t *a, uint32_t g, const char *kind, size_t kn,
                           const char *old, size_t on, const char *nw, size_t nn) {
    int rc;
    a->counts[MOY_ROW_ARTWORK_FOLLOW]++;
    if (holding(a, g, MOY_ROLE_ARTWORK, &rc) == NULL) {
        return rc;
    }
    if (nn == 0u || nn > MOY_APP_DOC_MAX || kn > MOY_APP_DOC_MAX || on > MOY_APP_DOC_MAX) {
        return MOY_APP_BAD;
    }
    char row[2u * MOY_APP_DOC_MAX + 2u];
    size_t rn = 0;
    row[rn++] = '"';
    for (size_t i = 0; i < nn; i++) {
        unsigned char ch = (unsigned char)nw[i];
        if (ch < 0x20u) {
            return MOY_APP_BAD;
        }
        if (ch == '"' || ch == '\\') {
            row[rn++] = '\\';
        }
        row[rn++] = (char)ch;
    }
    row[rn++] = '"';
    char cur[MOY_APP_DOC_MAX];
    long n = art_row(a, ART_DOC, cur, sizeof(cur));
    if (n < 0 || (size_t)n != on || memcmp(cur, old, on) != 0) {
        return MOY_APP_ABSENT;
    }
    long k = art_row(a, ART_KIND, cur, sizeof(cur));
    const char *have = cur;
    if (k < 0) {
        have = DRAWINGS;
        k = (long)(sizeof(DRAWINGS) - 1u);
    }
    if ((size_t)k != kn || memcmp(have, kind, kn) != 0) {
        return MOY_APP_ABSENT;
    }
    rc = moy_settings_set(a->prefs, ART_DOC, sizeof(ART_DOC) - 1u, row, rn);
    if (rc != MOY_SETTINGS_OK) {
        return rc == MOY_SETTINGS_NOMEM ? MOY_APP_NOMEM : MOY_APP_BAD;
    }
    moy_settings_flush(a->prefs);
    return MOY_APP_OK;
}

// -- the user-files store -----------------------------------------------------------------

int moy_app_store_bind(moy_appabi_t *a, const moy_uf_ops_t *ops, const char *root,
                       size_t n, int readable, int writable) {
    if (n > MOY_APP_ROOT_MAX || (n > 0u && memchr(root, 0, n) != NULL)) {
        return MOY_APP_BAD;
    }
    memcpy(a->root, root, n);
    a->root[n] = 0;
    a->uf = n > 0u ? ops : NULL;
    a->readable = a->uf != NULL && readable;
    a->writable = a->readable && writable;
    return MOY_APP_OK;
}

void moy_app_store_unwind(moy_appabi_t *a) {
    if (a->gated >= 0 && a->uf != NULL) {
        a->uf->gate_leave(a->gated);
    }
    a->gated = -1;
}

int moy_app_why(const moy_appabi_t *a) {
    return a->why;
}

void moy_app_buf_free(moy_appabi_t *a, moy_buf_t *b) {
    if (b->p != NULL && a->uf != NULL) {
        a->uf->buf_free(b);
    }
    b->p = NULL;
    b->n = 0;
}

uint32_t moy_app_files_end_all(moy_appabi_t *a) {
    uint32_t n = a->sessions;
    a->sessions = 0u;
    return n;
}

uint32_t moy_app_copy_gen(const moy_appabi_t *a) {
    return a->copy_gen;
}

// A layer code as the ABI's.
static int uf_rc(moy_appabi_t *a, int rc) {
    if (rc == 0) {
        return MOY_APP_OK;
    }
    if (rc == MOY_UF_NONE) {
        return MOY_APP_ABSENT;
    }
    if (rc == MOY_UF_BAD) {
        return MOY_APP_BAD;
    }
    if (rc == MOY_ENOMEM) {
        return MOY_APP_NOMEM;
    }
    a->why = rc;
    return MOY_APP_IO;
}

enum { NEED_LAYER = 0, NEED_READ = 1, NEED_WRITE = 2 };

// The grant of a files row, counted: NULL and `*rc` when it cannot run. A
// kind the grant was not made for is DENIED; `need` is what the store must do.
static const moy_grant_t *files_row(moy_appabi_t *a, uint32_t g, int row,
                                    const char *kind, int need, int *rc) {
    a->counts[row]++;
    const moy_grant_t *gr = holding(a, g, MOY_ROLE_FILES, rc);
    if (gr == NULL) {
        return NULL;
    }
    if (kind != NULL && gr->kind >= 0 && strcmp(kind, KIND_NAMES[(int)gr->kind]) != 0) {
        *rc = MOY_APP_DENIED;
        return NULL;
    }
    if (a->uf == NULL) {
        *rc = need == NEED_LAYER ? MOY_APP_ABSENT : MOY_APP_NOSTORE;
        return NULL;
    }
    if ((need == NEED_READ && !a->readable) || (need == NEED_WRITE && !a->writable)) {
        *rc = MOY_APP_NOSTORE;
        return NULL;
    }
    return gr;
}

// The gate around one store op.
static void gate_on(moy_appabi_t *a) {
    a->gated = a->uf->gate_enter(a->root);
}

static int gate_off(moy_appabi_t *a, int rc) {
    a->uf->gate_leave(a->gated);
    a->gated = -1;
    return uf_rc(a, rc);
}

int32_t moy_app_files_readable(moy_appabi_t *a, uint32_t g) {
    int rc;
    a->counts[MOY_ROW_FILES_READABLE]++;
    if (holding(a, g, MOY_ROLE_FILES, &rc) == NULL) {
        return -rc;
    }
    return a->readable;
}

int32_t moy_app_files_ready(moy_appabi_t *a, uint32_t g) {
    int rc;
    a->counts[MOY_ROW_FILES_READY]++;
    if (holding(a, g, MOY_ROLE_FILES, &rc) == NULL) {
        return -rc;
    }
    return a->writable;
}

int moy_app_files_begin(moy_appabi_t *a, uint32_t g) {
    int rc;
    if (files_row(a, g, MOY_ROW_FILES_BEGIN, NULL, NEED_WRITE, &rc) == NULL) {
        return rc;
    }
    a->sessions++;
    return MOY_APP_OK;
}

int moy_app_files_end(moy_appabi_t *a, uint32_t g) {
    int rc;
    a->counts[MOY_ROW_FILES_END]++;
    if (holding(a, g, MOY_ROLE_FILES, &rc) == NULL) {
        return rc;
    }
    if (a->sessions > 0u) {
        a->sessions--;
    }
    return MOY_APP_OK;
}

int moy_app_files_list(moy_appabi_t *a, uint32_t g, const char *kind,
                       moy_buf_t *out, uint32_t *count) {
    int rc;
    out->p = NULL;
    out->n = 0;
    *count = 0;
    if (files_row(a, g, MOY_ROW_FILES_LIST, kind, NEED_READ, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->list(a->root, kind, out, count));
}

int moy_app_files_count(moy_appabi_t *a, uint32_t g, const char *kind, uint32_t *n) {
    int rc;
    *n = 0;
    if (files_row(a, g, MOY_ROW_FILES_COUNT, kind, NEED_READ, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->count(a->root, kind, n));
}

int moy_app_files_load(moy_appabi_t *a, uint32_t g, const char *kind,
                       const char *name, moy_buf_t *out, int *binary) {
    int rc;
    out->p = NULL;
    out->n = 0;
    *binary = 0;
    if (files_row(a, g, MOY_ROW_FILES_LOAD, kind, NEED_READ, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->load(a->root, kind, name, out, binary));
}

int moy_app_files_save(moy_appabi_t *a, uint32_t g, const char *kind,
                       const char *name, const char *data, size_t n, char *name_out) {
    int rc;
    name_out[0] = 0;
    if (files_row(a, g, MOY_ROW_FILES_SAVE, kind, NEED_WRITE, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->save(a->root, kind, name, data, n, name_out));
}

typedef int (*named_fn)(const char *, const char *, const char *, char *);

static int named(moy_appabi_t *a, uint32_t g, int row, named_fn fn, const char *kind,
                 const char *name, char *name_out) {
    int rc;
    name_out[0] = 0;
    if (files_row(a, g, row, kind, NEED_WRITE, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, fn(a->root, kind, name, name_out));
}

int moy_app_files_delete(moy_appabi_t *a, uint32_t g, const char *kind,
                         const char *name, char *name_out) {
    return named(a, g, MOY_ROW_FILES_DELETE, a->uf != NULL ? a->uf->del : NULL, kind,
                 name, name_out);
}

int moy_app_files_duplicate(moy_appabi_t *a, uint32_t g, const char *kind,
                            const char *name, char *name_out) {
    return named(a, g, MOY_ROW_FILES_DUPLICATE, a->uf != NULL ? a->uf->duplicate : NULL,
                 kind, name, name_out);
}

int moy_app_files_restore(moy_appabi_t *a, uint32_t g, const char *kind,
                          const char *name, char *name_out) {
    return named(a, g, MOY_ROW_FILES_RESTORE, a->uf != NULL ? a->uf->restore : NULL,
                 kind, name, name_out);
}

int moy_app_files_rename(moy_appabi_t *a, uint32_t g, const char *kind,
                         const char *name, const char *title, char *name_out) {
    int rc;
    name_out[0] = 0;
    if (files_row(a, g, MOY_ROW_FILES_RENAME, kind, NEED_WRITE, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->rename(a->root, kind, name, title, name_out));
}

int moy_app_files_new_name(moy_appabi_t *a, uint32_t g, const char *kind,
                           const char *title, char *name_out) {
    int rc;
    name_out[0] = 0;
    if (files_row(a, g, MOY_ROW_FILES_NEW_NAME, kind, NEED_READ, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    rc = title != NULL && *title ? a->uf->free_name(a->root, kind, title, name_out)
         : a->uf->new_name(a->root, kind, NULL, name_out);
    return gate_off(a, rc);
}

int moy_app_files_trash_list(moy_appabi_t *a, uint32_t g, moy_buf_t *out,
                             uint32_t *count) {
    int rc;
    out->p = NULL;
    out->n = 0;
    *count = 0;
    if (files_row(a, g, MOY_ROW_FILES_TRASH_LIST, NULL, NEED_READ, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->trash_list(a->root, out, count));
}

int moy_app_files_empty_trash(moy_appabi_t *a, uint32_t g) {
    int rc;
    if (files_row(a, g, MOY_ROW_FILES_EMPTY_TRASH, NULL, NEED_WRITE, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->prune_trash(a->root, 0u));
}

int moy_app_files_history(moy_appabi_t *a, uint32_t g, const char *kind,
                          const char *name, moy_buf_t *out) {
    int rc;
    out->p = NULL;
    out->n = 0;
    if (files_row(a, g, MOY_ROW_FILES_HISTORY, kind, NEED_READ, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->history(a->root, kind, name, out));
}

int moy_app_files_history_ops(moy_appabi_t *a, uint32_t g, const char *kind,
                              const char *name, moy_buf_t *out) {
    int rc;
    out->p = NULL;
    out->n = 0;
    if (files_row(a, g, MOY_ROW_FILES_HISTORY_OPS, kind, NEED_READ, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->history_ops(a->root, kind, name, out));
}

int moy_app_files_history_commit(moy_appabi_t *a, uint32_t g, const char *kind,
                                 const char *name, const char *ops, size_t ops_n,
                                 const char *kf, size_t kf_n, int *prune_err) {
    int rc;
    *prune_err = 0;
    if (files_row(a, g, MOY_ROW_FILES_HISTORY_COMMIT, kind, NEED_WRITE, &rc) == NULL) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->history_commit(a->root, kind, name, ops, ops_n, kf, kf_n,
                                             prune_err));
}

int moy_app_files_encode_image(moy_appabi_t *a, uint32_t g, uint32_t w, uint32_t h,
                               const uint8_t *pix, size_t n, moy_buf_t *out) {
    int rc;
    out->p = NULL;
    out->n = 0;
    if (files_row(a, g, MOY_ROW_FILES_ENCODE_IMAGE, NULL, NEED_LAYER, &rc) == NULL) {
        return rc;
    }
    return uf_rc(a, a->uf->encode_image(w, h, pix, n, out));
}

int moy_app_files_decode_image(moy_appabi_t *a, uint32_t g, const char *text,
                               size_t n, moy_buf_t *pix, uint32_t *w, uint32_t *h) {
    int rc;
    pix->p = NULL;
    pix->n = 0;
    *w = *h = 0;
    if (files_row(a, g, MOY_ROW_FILES_DECODE_IMAGE, NULL, NEED_LAYER, &rc) == NULL) {
        return rc;
    }
    return uf_rc(a, a->uf->decode_image(text, n, pix, w, h));
}

int moy_app_files_decode_cover(moy_appabi_t *a, uint32_t g, const uint8_t *data,
                               size_t n, moy_buf_t *pix) {
    int rc;
    pix->p = NULL;
    pix->n = 0;
    if (files_row(a, g, MOY_ROW_FILES_DECODE_COVER, NULL, NEED_LAYER, &rc) == NULL) {
        return rc;
    }
    return uf_rc(a, a->uf->decode_cover(data, n, pix));
}

int moy_app_files_encode_cover(moy_appabi_t *a, uint32_t g, const uint8_t *pix,
                               size_t n, moy_buf_t *out) {
    int rc;
    out->p = NULL;
    out->n = 0;
    if (files_row(a, g, MOY_ROW_FILES_ENCODE_COVER, NULL, NEED_LAYER, &rc) == NULL) {
        return rc;
    }
    return uf_rc(a, a->uf->encode_cover(pix, n, out));
}

int moy_app_files_sig(moy_appabi_t *a, uint32_t g, const char *text, size_t n,
                      uint32_t *sig) {
    int rc;
    *sig = 0;
    if (files_row(a, g, MOY_ROW_FILES_SIG, NULL, NEED_LAYER, &rc) == NULL) {
        return rc;
    }
    *sig = a->uf->sig(text, n);
    return MOY_APP_OK;
}

int moy_app_files_stamp(moy_appabi_t *a, uint32_t g, const char *blob, size_t n,
                        const char *kind, const char *name, uint32_t sig,
                        moy_buf_t *out) {
    int rc;
    out->p = NULL;
    out->n = 0;
    if (files_row(a, g, MOY_ROW_FILES_STAMP, NULL, NEED_LAYER, &rc) == NULL) {
        return rc;
    }
    return uf_rc(a, a->uf->stamp(blob, n, kind, name, sig, out));
}

int moy_app_files_encode_text(moy_appabi_t *a, uint32_t g) {
    int rc;
    return files_row(a, g, MOY_ROW_FILES_ENCODE_TEXT, NULL, NEED_LAYER, &rc) != NULL
           ? MOY_APP_OK : rc;
}

int moy_app_files_decode_text(moy_appabi_t *a, uint32_t g) {
    int rc;
    return files_row(a, g, MOY_ROW_FILES_DECODE_TEXT, NULL, NEED_LAYER, &rc) != NULL
           ? MOY_APP_OK : rc;
}

int moy_app_files_provenance(moy_appabi_t *a, uint32_t g, const char *blob, size_t n,
                             moy_buf_t *src, int64_t *sig) {
    int rc;
    src->p = NULL;
    src->n = 0;
    *sig = 0;
    if (files_row(a, g, MOY_ROW_FILES_PROVENANCE, NULL, NEED_LAYER, &rc) == NULL) {
        return rc;
    }
    return uf_rc(a, a->uf->provenance(blob, n, src, sig));
}

// -- wallpaper ------------------------------------------------------------------------------

static int wallpaper_row(moy_appabi_t *a, uint32_t g, int row, int write) {
    int rc;
    a->counts[row]++;
    if (holding(a, g, MOY_ROLE_WALLPAPER, &rc) == NULL) {
        return rc;
    }
    return a->uf == NULL || !(write ? a->writable : a->readable) ? MOY_APP_NOSTORE
           : MOY_APP_OK;
}

int moy_app_wallpaper_load_copy(moy_appabi_t *a, uint32_t g, moy_buf_t *out) {
    out->p = NULL;
    out->n = 0;
    int rc = wallpaper_row(a, g, MOY_ROW_WALLPAPER_LOAD_COPY, 0);
    if (rc != MOY_APP_OK) {
        return rc;
    }
    gate_on(a);
    return gate_off(a, a->uf->copy_load(a->root, out));
}

int moy_app_wallpaper_save_copy(moy_appabi_t *a, uint32_t g, const char *data,
                                size_t n) {
    int rc = wallpaper_row(a, g, MOY_ROW_WALLPAPER_SAVE_COPY, 1);
    if (rc != MOY_APP_OK) {
        return rc;
    }
    gate_on(a);
    rc = gate_off(a, a->uf->copy_save(a->root, data, n));
    if (rc == MOY_APP_OK) {
        a->copy_gen++;
    }
    return rc;
}

// -- the rows served in Python ------------------------------------------------------------

int moy_app_holds(const moy_appabi_t *a, uint32_t g, int role) {
    int rc;
    holding(a, g, role, &rc);
    return rc;
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
    seq_open(a);
    memcpy(a->clip, text, n);
    a->clip_len = (uint32_t)n;
    a->clip_kind = MOY_CLIP_TEXT;
    a->clip_seq++;
    seq_close(a);
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

// -- the ROLE door -------------------------------------------------------------------

// The role table, row for row: each row's "role.verb", its role and its C row
// (MOY_APP_SHELL for a row the Python console serves). tests/test_roles.py
// holds it to roles.json.
static const struct { const char *name; int8_t role, c_row; } TABLE[MOY_APP_TABLE_N] = {
    {"damage.all", MOY_ROLE_DAMAGE, MOY_ROW_DAMAGE_ALL},
    {"damage.again", MOY_ROLE_DAMAGE, MOY_ROW_DAMAGE_AGAIN},
    {"surface.canvas", MOY_ROLE_SURFACE, MOY_ROW_SURFACE_CANVAS},
    {"surface.size", MOY_ROLE_SURFACE, MOY_ROW_SURFACE_SIZE},
    {"surface.font_scale", MOY_ROLE_SURFACE, MOY_ROW_SURFACE_FONT_SCALE},
    {"surface.chrome_scale", MOY_ROLE_SURFACE, MOY_ROW_SURFACE_CHROME_SCALE},
    {"surface.windowed", MOY_ROLE_SURFACE, MOY_ROW_SURFACE_WINDOWED},
    {"surface.bar_h", MOY_ROLE_SURFACE, MOY_ROW_SURFACE_BAR_H},
    {"surface.pointer", MOY_ROLE_SURFACE, MOY_ROW_SURFACE_POINTER},
    {"surface.glyph", MOY_ROLE_SURFACE, MOY_APP_SHELL},
    {"theme.colors", MOY_ROLE_THEME, MOY_ROW_THEME_COLORS},
    {"theme.token", MOY_ROLE_THEME, MOY_ROW_THEME_TOKEN},
    {"theme.gen", MOY_ROLE_THEME, MOY_ROW_THEME_GEN},
    {"theme.light", MOY_ROLE_THEME, MOY_ROW_THEME_LIGHT},
    {"theme.name", MOY_ROLE_THEME, MOY_ROW_THEME_NAME},
    {"theme.variant", MOY_ROLE_THEME, MOY_ROW_THEME_VARIANT},
    {"theme.skin", MOY_ROLE_THEME, MOY_ROW_THEME_SKIN},
    {"theme.set", MOY_ROLE_THEME, MOY_APP_SHELL},
    {"theme.set_variant", MOY_ROLE_THEME, MOY_APP_SHELL},
    {"theme.set_skin", MOY_ROLE_THEME, MOY_APP_SHELL},
    {"files.readable", MOY_ROLE_FILES, MOY_ROW_FILES_READABLE},
    {"files.ready", MOY_ROLE_FILES, MOY_ROW_FILES_READY},
    {"files.begin", MOY_ROLE_FILES, MOY_ROW_FILES_BEGIN},
    {"files.end", MOY_ROLE_FILES, MOY_ROW_FILES_END},
    {"files.list", MOY_ROLE_FILES, MOY_ROW_FILES_LIST},
    {"files.count", MOY_ROLE_FILES, MOY_ROW_FILES_COUNT},
    {"files.load", MOY_ROLE_FILES, MOY_ROW_FILES_LOAD},
    {"files.save", MOY_ROLE_FILES, MOY_ROW_FILES_SAVE},
    {"files.delete", MOY_ROLE_FILES, MOY_ROW_FILES_DELETE},
    {"files.duplicate", MOY_ROLE_FILES, MOY_ROW_FILES_DUPLICATE},
    {"files.rename", MOY_ROLE_FILES, MOY_ROW_FILES_RENAME},
    {"files.new_name", MOY_ROLE_FILES, MOY_ROW_FILES_NEW_NAME},
    {"files.trash_list", MOY_ROLE_FILES, MOY_ROW_FILES_TRASH_LIST},
    {"files.restore", MOY_ROLE_FILES, MOY_ROW_FILES_RESTORE},
    {"files.empty_trash", MOY_ROLE_FILES, MOY_ROW_FILES_EMPTY_TRASH},
    {"files.history", MOY_ROLE_FILES, MOY_ROW_FILES_HISTORY},
    {"files.history_ops", MOY_ROLE_FILES, MOY_ROW_FILES_HISTORY_OPS},
    {"files.history_commit", MOY_ROLE_FILES, MOY_ROW_FILES_HISTORY_COMMIT},
    {"files.encode_image", MOY_ROLE_FILES, MOY_ROW_FILES_ENCODE_IMAGE},
    {"files.decode_image", MOY_ROLE_FILES, MOY_ROW_FILES_DECODE_IMAGE},
    {"files.decode_cover", MOY_ROLE_FILES, MOY_ROW_FILES_DECODE_COVER},
    {"files.encode_cover", MOY_ROLE_FILES, MOY_ROW_FILES_ENCODE_COVER},
    {"files.sig", MOY_ROLE_FILES, MOY_ROW_FILES_SIG},
    {"files.stamp", MOY_ROLE_FILES, MOY_ROW_FILES_STAMP},
    {"files.encode_text", MOY_ROLE_FILES, MOY_ROW_FILES_ENCODE_TEXT},
    {"files.decode_text", MOY_ROLE_FILES, MOY_ROW_FILES_DECODE_TEXT},
    {"files.provenance", MOY_ROLE_FILES, MOY_ROW_FILES_PROVENANCE},
    {"carts.readable", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.ready", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.begin", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.end", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.all", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.can_journal", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.slug", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.create", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.journal", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.rescan", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.hydrate", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.load_deck", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.save_deck", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.save_code", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.images", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.save_image", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"carts.encode_image", MOY_ROLE_CARTS, MOY_APP_SHELL},
    {"nav.open_app", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"nav.is_system_app", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"nav.projects", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"nav.edit", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"nav.open_image", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"nav.open_text", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"nav.edit_file", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"nav.play", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"nav.run_script", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"nav.text_mode", MOY_ROLE_NAV, MOY_APP_SHELL},
    {"prefs.get", MOY_ROLE_PREFS, MOY_ROW_PREFS_GET},
    {"prefs.set", MOY_ROLE_PREFS, MOY_ROW_PREFS_SET},
    {"prefs.clear", MOY_ROLE_PREFS, MOY_ROW_PREFS_CLEAR},
    {"notify.achieve", MOY_ROLE_NOTIFY, MOY_APP_SHELL},
    {"wallpaper.current", MOY_ROLE_WALLPAPER, MOY_APP_SHELL},
    {"wallpaper.carts", MOY_ROLE_WALLPAPER, MOY_APP_SHELL},
    {"wallpaper.fills", MOY_ROLE_WALLPAPER, MOY_APP_SHELL},
    {"wallpaper.id_for", MOY_ROLE_WALLPAPER, MOY_APP_SHELL},
    {"wallpaper.title", MOY_ROLE_WALLPAPER, MOY_APP_SHELL},
    {"wallpaper.select", MOY_ROLE_WALLPAPER, MOY_APP_SHELL},
    {"wallpaper.preview", MOY_ROLE_WALLPAPER, MOY_APP_SHELL},
    {"wallpaper.thumbnail", MOY_ROLE_WALLPAPER, MOY_APP_SHELL},
    {"wallpaper.load_copy", MOY_ROLE_WALLPAPER, MOY_ROW_WALLPAPER_LOAD_COPY},
    {"wallpaper.save_copy", MOY_ROLE_WALLPAPER, MOY_ROW_WALLPAPER_SAVE_COPY},
    {"artwork.current", MOY_ROLE_ARTWORK, MOY_ROW_ARTWORK_CURRENT},
    {"artwork.follow", MOY_ROLE_ARTWORK, MOY_ROW_ARTWORK_FOLLOW},
    {"clipboard.put_text", MOY_ROLE_CLIPBOARD, MOY_ROW_CLIPBOARD_PUT_TEXT},
    {"clipboard.text", MOY_ROLE_CLIPBOARD, MOY_ROW_CLIPBOARD_TEXT},
    {"clipboard.kind", MOY_ROLE_CLIPBOARD, MOY_ROW_CLIPBOARD_KIND},
    {"clipboard.seq", MOY_ROLE_CLIPBOARD, MOY_ROW_CLIPBOARD_SEQ},
    {"install.hold", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.release", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.fit", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.memory", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.chip", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.runtimes", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.home", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.can_pick", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.pick", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.root", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.writable", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.op", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.rescan", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.free", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.find", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.net", MOY_ROLE_INSTALL, MOY_APP_SHELL},
    {"install.keep", MOY_ROLE_INSTALL, MOY_APP_SHELL},
};

int moy_app_table_role(uint32_t row) {
    return row < MOY_APP_TABLE_N ? TABLE[row].role : -1;
}

int moy_app_table_c_row(uint32_t row) {
    return row < MOY_APP_TABLE_N ? TABLE[row].c_row : -1;
}

const char *moy_app_table_name(uint32_t row) {
    return row < MOY_APP_TABLE_N ? TABLE[row].name : NULL;
}

static moy_app_door_fn s_door;

void moy_app_door_bind(moy_app_door_fn fn) {
    s_door = fn;
}

// The packed arguments, read in order.
typedef struct {
    const uint8_t *p;
    size_t n, at;
    int bad;
} args_t;

static const uint8_t *arg_field(args_t *f, size_t *len) {
    if (f->n - f->at < 4u) {
        f->bad = 1;
        *len = 0;
        return NULL;
    }
    const uint8_t *q = f->p + f->at;
    size_t m = (size_t)q[0] | (size_t)q[1] << 8 | (size_t)q[2] << 16 | (size_t)q[3] << 24;
    if (m > f->n - f->at - 4u) {
        f->bad = 1;
        *len = 0;
        return NULL;
    }
    f->at += 4u + m;
    *len = m;
    return q + 4;
}

static int32_t arg_int(args_t *f) {
    size_t m;
    const uint8_t *q = arg_field(f, &m);
    if (q == NULL || m != 4u) {
        f->bad = 1;
        return 0;
    }
    return (int32_t)((uint32_t)q[0] | (uint32_t)q[1] << 8 | (uint32_t)q[2] << 16
                     | (uint32_t)q[3] << 24);
}

// A text field NUL-terminated into `out` (MOY_UF_NAME_MAX + 1 bytes): NULL for
// an empty one where `optional`.
static const char *arg_text(args_t *f, char out[MOY_UF_NAME_MAX + 1u], int optional) {
    size_t m;
    const uint8_t *q = arg_field(f, &m);
    if (q == NULL || m > MOY_UF_NAME_MAX || memchr(q, 0, m) != NULL) {
        f->bad = 1;
        return NULL;
    }
    memcpy(out, q, m);
    out[m] = 0;
    return optional && m == 0u ? NULL : out;
}

static int32_t ans_put(uint8_t *ans, size_t cap, const void *data, size_t len) {
    if (len > cap || len > 0x7fffffffu) {
        return -MOY_APP_FULL;
    }
    if (len) {
        memcpy(ans, data, len);
    }
    return (int32_t)len;
}

static int32_t ans_rc(int rc) {
    return rc == MOY_APP_OK ? 0 : -rc;
}

// A blob the layer allocated: copied out on OK, freed either way.
static int32_t ans_buf(moy_appabi_t *a, int rc, moy_buf_t *b, uint8_t *ans, size_t cap) {
    int32_t v = rc == MOY_APP_OK ? ans_put(ans, cap, b->p, b->n) : -rc;
    moy_app_buf_free(a, b);
    return v;
}

static int32_t ans_name(int rc, const char *name, uint8_t *ans, size_t cap) {
    return rc == MOY_APP_OK ? ans_put(ans, cap, name, strlen(name)) : -rc;
}

static void le32(uint8_t *q, uint32_t v) {
    q[0] = (uint8_t)v;
    q[1] = (uint8_t)(v >> 8);
    q[2] = (uint8_t)(v >> 16);
    q[3] = (uint8_t)(v >> 24);
}

// The C rows that take no argument: a field handed one is BAD.
static int no_args(int row) {
    switch (row) {
        case MOY_ROW_THEME_TOKEN:
        case MOY_ROW_FILES_LIST: case MOY_ROW_FILES_COUNT: case MOY_ROW_FILES_LOAD:
        case MOY_ROW_FILES_HISTORY: case MOY_ROW_FILES_HISTORY_OPS:
        case MOY_ROW_FILES_SAVE: case MOY_ROW_FILES_DELETE: case MOY_ROW_FILES_DUPLICATE:
        case MOY_ROW_FILES_RESTORE: case MOY_ROW_FILES_RENAME: case MOY_ROW_FILES_NEW_NAME:
        case MOY_ROW_FILES_HISTORY_COMMIT:
        case MOY_ROW_PREFS_GET: case MOY_ROW_PREFS_SET: case MOY_ROW_PREFS_CLEAR:
        case MOY_ROW_WALLPAPER_SAVE_COPY: case MOY_ROW_ARTWORK_FOLLOW:
        case MOY_ROW_CLIPBOARD_PUT_TEXT:
            return 0;
        default:
            return 1;
    }
}

// The C rows a compiled app reaches through the door.
static int32_t door_c(moy_appabi_t *a, uint32_t g, int row, args_t *f, uint8_t *ans,
                      size_t cap) {
    char k[MOY_UF_NAME_MAX + 1u], nm[MOY_UF_NAME_MAX + 1u], t[MOY_UF_NAME_MAX + 1u];
    char out[MOY_UF_NAME_MAX + 1u];
    moy_buf_t b = {NULL, 0};
    const uint8_t *d;
    size_t dn, en, len = 0;
    const uint8_t *e;
    uint32_t cnt;
    int rc, bin;
    switch (row) {
        case MOY_ROW_DAMAGE_ALL:
            return ans_rc(moy_app_damage_all(a, g));
        case MOY_ROW_DAMAGE_AGAIN:
            return ans_rc(moy_app_damage_again(a, g));
        case MOY_ROW_SURFACE_SIZE: {
            int32_t w = 0, h = 0;
            uint8_t q[8];
            rc = moy_app_surface_size(a, g, &w, &h);
            if (rc != MOY_APP_OK) {
                return -rc;
            }
            le32(q, (uint32_t)w);
            le32(q + 4, (uint32_t)h);
            return ans_put(ans, cap, q, sizeof q);
        }
        case MOY_ROW_SURFACE_FONT_SCALE:
            return moy_app_surface_font_scale(a, g);
        case MOY_ROW_SURFACE_CHROME_SCALE:
            return moy_app_surface_chrome_scale(a, g);
        case MOY_ROW_SURFACE_WINDOWED:
            return moy_app_surface_windowed(a, g);
        case MOY_ROW_SURFACE_BAR_H:
            return moy_app_surface_bar_h(a, g);
        case MOY_ROW_SURFACE_POINTER: {
            int32_t p[5] = {0, 0, 0, 0, 0};
            uint8_t q[20];
            rc = moy_app_surface_pointer(a, g, p);
            if (rc != MOY_APP_OK) {
                return -rc;
            }
            for (int i = 0; i < 5; i++) {
                le32(q + 4 * i, (uint32_t)p[i]);
            }
            return ans_put(ans, cap, q, sizeof q);
        }
        case MOY_ROW_THEME_TOKEN: {
            int32_t v = 0, r = arg_int(f);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            rc = moy_app_theme_token(a, g, r, &v);
            if (rc != MOY_APP_OK) {
                return -rc;
            }
            return v == MOY_TOKEN_ABSENT ? -MOY_APP_ABSENT : v;
        }
        case MOY_ROW_THEME_GEN:
            return moy_app_theme_gen(a, g);
        case MOY_ROW_THEME_LIGHT:
            return moy_app_theme_light(a, g);
        case MOY_ROW_THEME_NAME:
        case MOY_ROW_THEME_VARIANT:
        case MOY_ROW_THEME_SKIN:
            rc = (row == MOY_ROW_THEME_NAME ? moy_app_theme_name
                  : row == MOY_ROW_THEME_VARIANT ? moy_app_theme_variant
                  : moy_app_theme_skin)(a, g, out, sizeof out, &len);
            return rc == MOY_APP_OK ? ans_put(ans, cap, out, len) : -rc;
        case MOY_ROW_FILES_READABLE:
            return moy_app_files_readable(a, g);
        case MOY_ROW_FILES_READY:
            return moy_app_files_ready(a, g);
        case MOY_ROW_FILES_BEGIN:
            return ans_rc(moy_app_files_begin(a, g));
        case MOY_ROW_FILES_END:
            return ans_rc(moy_app_files_end(a, g));
        case MOY_ROW_FILES_LIST:
            arg_text(f, k, 0);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            rc = moy_app_files_list(a, g, k, &b, &cnt);
            return ans_buf(a, rc, &b, ans, cap);
        case MOY_ROW_FILES_COUNT:
            arg_text(f, k, 0);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            rc = moy_app_files_count(a, g, k, &cnt);
            return rc == MOY_APP_OK ? (int32_t)(cnt & 0x7fffffffu) : -rc;
        case MOY_ROW_FILES_LOAD:
        case MOY_ROW_FILES_HISTORY:
        case MOY_ROW_FILES_HISTORY_OPS:
            arg_text(f, k, 0);
            arg_text(f, nm, 0);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            rc = row == MOY_ROW_FILES_LOAD ? moy_app_files_load(a, g, k, nm, &b, &bin)
                 : row == MOY_ROW_FILES_HISTORY ? moy_app_files_history(a, g, k, nm, &b)
                 : moy_app_files_history_ops(a, g, k, nm, &b);
            return ans_buf(a, rc, &b, ans, cap);
        case MOY_ROW_FILES_SAVE:
            arg_text(f, k, 0);
            arg_text(f, nm, 0);
            d = arg_field(f, &dn);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            rc = moy_app_files_save(a, g, k, nm, (const char *)d, dn, out);
            return ans_name(rc, out, ans, cap);
        case MOY_ROW_FILES_DELETE:
        case MOY_ROW_FILES_DUPLICATE:
        case MOY_ROW_FILES_RESTORE:
            arg_text(f, k, 0);
            arg_text(f, nm, 0);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            rc = (row == MOY_ROW_FILES_DELETE ? moy_app_files_delete
                  : row == MOY_ROW_FILES_DUPLICATE ? moy_app_files_duplicate
                  : moy_app_files_restore)(a, g, k, nm, out);
            return ans_name(rc, out, ans, cap);
        case MOY_ROW_FILES_RENAME:
            arg_text(f, k, 0);
            arg_text(f, nm, 0);
            arg_text(f, t, 0);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            return ans_name(moy_app_files_rename(a, g, k, nm, t, out), out, ans, cap);
        case MOY_ROW_FILES_NEW_NAME: {
            arg_text(f, k, 0);
            const char *title = arg_text(f, t, 1);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            return ans_name(moy_app_files_new_name(a, g, k, title, out), out, ans, cap);
        }
        case MOY_ROW_FILES_TRASH_LIST:
            rc = moy_app_files_trash_list(a, g, &b, &cnt);
            return ans_buf(a, rc, &b, ans, cap);
        case MOY_ROW_FILES_EMPTY_TRASH:
            return ans_rc(moy_app_files_empty_trash(a, g));
        case MOY_ROW_FILES_HISTORY_COMMIT: {
            int prune = 0;
            arg_text(f, k, 0);
            arg_text(f, nm, 0);
            d = arg_field(f, &dn);
            e = arg_field(f, &en);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            return ans_rc(moy_app_files_history_commit(
                a, g, k, nm, dn ? (const char *)d : NULL, dn, en ? (const char *)e : NULL,
                en, &prune));
        }
        case MOY_ROW_PREFS_GET:
            d = arg_field(f, &dn);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            rc = moy_app_prefs_get(a, g, (const char *)d, dn, (char *)ans, cap, &len);
            return rc != MOY_APP_OK ? -rc : len > cap ? -MOY_APP_FULL : (int32_t)len;
        case MOY_ROW_PREFS_SET:
            d = arg_field(f, &dn);
            e = arg_field(f, &en);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            return ans_rc(moy_app_prefs_set(a, g, (const char *)d, dn, (const char *)e, en));
        case MOY_ROW_PREFS_CLEAR:
            d = arg_field(f, &dn);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            return ans_rc(moy_app_prefs_clear(a, g, (const char *)d, dn));
        case MOY_ROW_WALLPAPER_LOAD_COPY:
            rc = moy_app_wallpaper_load_copy(a, g, &b);
            return ans_buf(a, rc, &b, ans, cap);
        case MOY_ROW_WALLPAPER_SAVE_COPY:
            d = arg_field(f, &dn);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            return ans_rc(moy_app_wallpaper_save_copy(a, g, (const char *)d, dn));
        case MOY_ROW_ARTWORK_CURRENT: {
            size_t kl = 0, nl = 0;
            rc = moy_app_artwork_current(a, g, k, sizeof k, &kl, nm, sizeof nm, &nl);
            if (rc != MOY_APP_OK) {
                return -rc;
            }
            if (8u + kl + nl > cap) {
                return -MOY_APP_FULL;
            }
            le32(ans, (uint32_t)kl);
            memcpy(ans + 4, k, kl);
            le32(ans + 4 + kl, (uint32_t)nl);
            memcpy(ans + 8 + kl, nm, nl);
            return (int32_t)(8u + kl + nl);
        }
        case MOY_ROW_ARTWORK_FOLLOW: {
            size_t on, nn;
            d = arg_field(f, &dn);
            const uint8_t *o = arg_field(f, &on);
            e = arg_field(f, &nn);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            return ans_rc(moy_app_artwork_follow(a, g, (const char *)d, dn, (const char *)o,
                                                 on, (const char *)e, nn));
        }
        case MOY_ROW_CLIPBOARD_PUT_TEXT:
            d = arg_field(f, &dn);
            if (f->bad || f->at != f->n) {
                return -MOY_APP_BAD;
            }
            return ans_rc(moy_app_clip_put_text(a, g, (const char *)d, dn));
        case MOY_ROW_CLIPBOARD_TEXT:
            rc = moy_app_clip_text(a, g, (char *)ans, cap, &len);
            return rc != MOY_APP_OK ? -rc : len > cap ? -MOY_APP_FULL : (int32_t)len;
        case MOY_ROW_CLIPBOARD_KIND:
            return moy_app_clip_kind(a, g);
        case MOY_ROW_CLIPBOARD_SEQ:
            return moy_app_clip_seq(a, g);
        default:
            // An object the Python binding hands out, or a codec over pixels.
            return -MOY_APP_BAD;
    }
}

int32_t moy_app_role(moy_appabi_t *a, uint32_t g, uint32_t row, const uint8_t *arg,
                     size_t n, uint8_t *ans, size_t cap) {
    if (row >= MOY_APP_TABLE_N || (n && arg == NULL) || (cap && ans == NULL)) {
        return -MOY_APP_BAD;
    }
    int c_row = TABLE[row].c_row;
    if (c_row != MOY_APP_SHELL) {
        if (n != 0u && no_args(c_row)) {
            return -MOY_APP_BAD;
        }
        args_t f = {arg, n, 0u, 0};
        int32_t v = door_c(a, g, c_row, &f, ans, cap);
        return f.bad ? -MOY_APP_BAD : v;
    }
    int rc = moy_app_holds(a, g, TABLE[row].role);
    if (rc != MOY_APP_OK) {
        return -rc;
    }
    return s_door == NULL ? -MOY_APP_NEEDS_VM : s_door(g, row, arg, n, ans, cap);
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

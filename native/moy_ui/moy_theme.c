// moy_theme: the theme loader (moy_theme.h).

#include <string.h>

#include "moy_theme.h"

#if __has_include("esp_heap_caps.h")
#include "esp_heap_caps.h"
#define THEME_ALLOC(n) heap_caps_malloc((n), MALLOC_CAP_SPIRAM)
#else
#include <stdlib.h>
#define THEME_ALLOC(n) malloc(n)
#endif

#define JSON_DEPTH 8          // the deepest nesting the reader follows
#define UNSET_ROW INT32_MIN

// -- the reader: strict JSON over a byte span, no tree ------------------------

typedef struct {
    const uint8_t *p, *end;
    int depth;
} rd_t;

static int peek(rd_t *r) {
    while (r->p < r->end && (*r->p == ' ' || *r->p == '\t' || *r->p == '\n'
                             || *r->p == '\r')) {
        r->p++;
    }
    return r->p < r->end ? *r->p : -1;
}

static int eat(rd_t *r, int c) {
    if (peek(r) == c) {
        r->p++;
        return 1;
    }
    return 0;
}

static int hex(int c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

// A string, its bytes into `out` (NUL-terminated) while they fit in `cap`.
// Its length, or -1: malformed. An escape stores its character; \uXXXX
// stores '?'. *over is set when the string did not fit.
static int str(rd_t *r, char *out, size_t cap, int *over) {
    *over = 0;
    if (!eat(r, '"')) {
        return -1;
    }
    size_t n = 0;
    while (r->p < r->end) {
        int c = *r->p++;
        if (c == '"') {
            if (out != NULL) {
                out[n < cap ? n : cap - 1] = 0;
            }
            return (int)n;
        }
        if (c < 0x20) {
            return -1;
        }
        if (c == '\\') {
            if (r->p >= r->end) {
                return -1;
            }
            c = *r->p++;
            switch (c) {
                case '"': case '\\': case '/': break;
                case 'b': c = '\b'; break;
                case 'f': c = '\f'; break;
                case 'n': c = '\n'; break;
                case 'r': c = '\r'; break;
                case 't': c = '\t'; break;
                case 'u':
                    if (r->end - r->p < 4 || !hex(r->p[0]) || !hex(r->p[1])
                        || !hex(r->p[2]) || !hex(r->p[3])) {
                        return -1;
                    }
                    r->p += 4;
                    c = '?';
                    break;
                default:
                    return -1;
            }
        }
        if (out != NULL && n + 1 < cap) {
            out[n] = (char)c;
        } else {
            *over = 1;
        }
        n++;
    }
    return -1;
}

enum { V_INT, V_TRUE, V_FALSE, V_OTHER };

static int word(rd_t *r, const char *w) {
    size_t n = strlen(w);
    if ((size_t)(r->end - r->p) < n || memcmp(r->p, w, n) != 0) {
        return 0;
    }
    r->p += n;
    return 1;
}

// A number. *iv gets an integer's value (saturated at +-1e6); a fraction or
// an exponent makes it V_OTHER. -1: malformed.
static int number(rd_t *r, int32_t *iv) {
    int neg = r->p < r->end && *r->p == '-';
    r->p += neg;
    if (r->p >= r->end || *r->p < '0' || *r->p > '9') {
        return -1;
    }
    int32_t v = 0;
    if (*r->p == '0') {
        r->p++;
    } else {
        while (r->p < r->end && *r->p >= '0' && *r->p <= '9') {
            v = v < 1000000 ? v * 10 + (*r->p - '0') : 1000000;
            r->p++;
        }
    }
    int kind = V_INT;
    if (r->p < r->end && *r->p == '.') {
        r->p++;
        if (r->p >= r->end || *r->p < '0' || *r->p > '9') {
            return -1;
        }
        while (r->p < r->end && *r->p >= '0' && *r->p <= '9') {
            r->p++;
        }
        kind = V_OTHER;
    }
    if (r->p < r->end && (*r->p == 'e' || *r->p == 'E')) {
        r->p++;
        if (r->p < r->end && (*r->p == '+' || *r->p == '-')) {
            r->p++;
        }
        if (r->p >= r->end || *r->p < '0' || *r->p > '9') {
            return -1;
        }
        while (r->p < r->end && *r->p >= '0' && *r->p <= '9') {
            r->p++;
        }
        kind = V_OTHER;
    }
    *iv = neg ? -v : v;
    return kind;
}

static int value(rd_t *r, int32_t *iv);

// An object or an array, walked and dropped. 0, or -1: malformed or too deep.
static int skip_nested(rd_t *r, int close) {
    if (++r->depth > JSON_DEPTH) {
        return -1;
    }
    if (!eat(r, close)) {
        for (;;) {
            int over;
            int32_t iv;
            if (close == '}' && (str(r, NULL, 0, &over) < 0 || !eat(r, ':'))) {
                return -1;
            }
            if (value(r, &iv) < 0) {
                return -1;
            }
            if (eat(r, ',')) {
                continue;
            }
            if (eat(r, close)) {
                break;
            }
            return -1;
        }
    }
    r->depth--;
    return 0;
}

// Any value: what kind it was (an integer's value in *iv), or -1: malformed.
static int value(rd_t *r, int32_t *iv) {
    int c = peek(r);
    int over;
    switch (c) {
        case '{': r->p++; return skip_nested(r, '}') < 0 ? -1 : V_OTHER;
        case '[': r->p++; return skip_nested(r, ']') < 0 ? -1 : V_OTHER;
        case '"': return str(r, NULL, 0, &over) < 0 ? -1 : V_OTHER;
        case 't': return word(r, "true") ? V_TRUE : -1;
        case 'f': return word(r, "false") ? V_FALSE : -1;
        case 'n': return word(r, "null") ? V_OTHER : -1;
        default: return number(r, iv);
    }
}

// -- the package -----------------------------------------------------------------

static int find_name(const char *const *names, int count, const char *name) {
    for (int i = 0; i < count; i++) {
        if (strcmp(names[i], name) == 0) {
            return i;
        }
    }
    return -1;
}

static int role_of(const char *name) {
    return find_name(moy_theme_role_names, MOY_THEME_ROLES, name);
}

static int name_ok(const char *s, int n, int over) {
    if (n < 1 || over || n > MOY_THEME_NAME_MAX) {
        return 0;
    }
    for (int i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) {
            return 0;
        }
    }
    return 1;
}

// One variant's roles. 0, or -1: malformed.
static int roles(rd_t *r, moy_theme_pkg_t *out, int variant) {
    if (eat(r, '}')) {
        return 0;
    }
    for (;;) {
        char key[MOY_THEME_NAME_MAX + 1];
        int over;
        int32_t iv = 0;
        if (str(r, key, sizeof(key), &over) < 0 || !eat(r, ':')) {
            return -1;
        }
        int kind = value(r, &iv);
        if (kind < 0) {
            return -1;
        }
        int role = over ? -1 : role_of(key);
        if (role < 0) {
            out->notes |= MOY_THEME_N_UNKNOWN;
        } else if (moy_theme_flag[role]) {
            if (kind == V_TRUE || kind == V_FALSE) {
                out->role[variant][role] = kind == V_TRUE;
            } else {
                out->notes |= MOY_THEME_N_DROPPED;
            }
        } else if (kind == V_INT) {
            if (iv < 0 || iv > 63) {
                iv = iv < 0 ? 0 : 63;
                out->notes |= MOY_THEME_N_CLAMPED;
            }
            out->role[variant][role] = (int8_t)iv;
        } else {
            out->notes |= MOY_THEME_N_DROPPED;
        }
        if (eat(r, ',')) {
            continue;
        }
        return eat(r, '}') ? 0 : -1;
    }
}

static int colors(rd_t *r, moy_theme_pkg_t *out) {
    if (eat(r, '}')) {
        return 0;
    }
    r->depth++;
    for (;;) {
        char key[8];
        int over;
        int32_t iv;
        if (str(r, key, sizeof(key), &over) < 0 || !eat(r, ':')) {
            return -1;
        }
        int variant = over ? -1 : strcmp(key, "dark") == 0 ? MOY_THEME_DARK
                      : strcmp(key, "light") == 0 ? MOY_THEME_LIGHT : -1;
        if (variant >= 0 && eat(r, '{')) {
            if (++r->depth > JSON_DEPTH || roles(r, out, variant) < 0) {
                return -1;
            }
            r->depth--;
        } else {
            int kind = value(r, &iv);
            if (kind < 0) {
                return -1;
            }
            out->notes |= variant >= 0 ? MOY_THEME_N_DROPPED : MOY_THEME_N_UNKNOWN;
        }
        if (eat(r, ',')) {
            continue;
        }
        r->depth--;
        return eat(r, '}') ? 0 : -1;
    }
}

// `metrics`: name -> an integer in its range. 0, or -1: malformed.
static int metrics(rd_t *r, moy_theme_pkg_t *out) {
    if (eat(r, '}')) {
        return 0;
    }
    r->depth++;
    for (;;) {
        char key[16];
        int over;
        int32_t iv = 0;
        if (str(r, key, sizeof(key), &over) < 0 || !eat(r, ':')) {
            return -1;
        }
        int kind = value(r, &iv);
        if (kind < 0) {
            return -1;
        }
        int m = over ? -1 : find_name(moy_theme_metric_names, MOY_THEME_METRICS, key);
        if (m < 0) {
            out->notes |= MOY_THEME_N_UNKNOWN;
        } else if (kind != V_INT) {
            out->notes |= MOY_THEME_N_DROPPED;
        } else {
            int lo = moy_theme_metric_range[m][0], hi = moy_theme_metric_range[m][1];
            if (iv < lo || iv > hi) {
                iv = iv < lo ? lo : hi;
                out->notes |= MOY_THEME_N_CLAMPED;
            }
            out->metric[m] = (int8_t)iv;
        }
        if (eat(r, ',')) {
            continue;
        }
        r->depth--;
        return eat(r, '}') ? 0 : -1;
    }
}

// A name-valued field: its index in `names`, -2 for a name not there, or -3
// for a value that is no string. -1: malformed.
static int name_field(rd_t *r, const char *const *names, int count) {
    char buf[MOY_THEME_NAME_MAX + 1];
    int over;
    int32_t iv;
    if (peek(r) != '"') {
        return value(r, &iv) < 0 ? -1 : -3;
    }
    if (str(r, buf, sizeof(buf), &over) < 0) {
        return -1;
    }
    int i = over ? -1 : find_name(names, count, buf);
    return i < 0 ? -2 : i;
}

// One state's effect: {"effect": NAME, "color": ROLE}. 0, or -1: malformed.
static int effect(rd_t *r, moy_theme_pkg_t *out, int kind, int state) {
    int eff = -1, role = -1, bad = 0;
    if (!eat(r, '}')) {
        r->depth++;
        for (;;) {
            char key[8];
            int over;
            int32_t iv;
            if (str(r, key, sizeof(key), &over) < 0 || !eat(r, ':')) {
                return -1;
            }
            int v;
            if (!over && strcmp(key, "effect") == 0) {
                v = name_field(r, moy_theme_effect_names, MOY_THEME_EFFECTS);
                eff = v;
            } else if (!over && strcmp(key, "color") == 0) {
                v = name_field(r, moy_theme_role_names, MOY_THEME_ROLES);
                role = v;
            } else {
                v = value(r, &iv) < 0 ? -1 : 0;
                out->notes |= MOY_THEME_N_UNKNOWN;
            }
            if (v == -1) {
                return -1;
            }
            bad |= v < -1;
            if (eat(r, ',')) {
                continue;
            }
            if (!eat(r, '}')) {
                return -1;
            }
            r->depth--;
            break;
        }
    }
    if (kind < 0 || state < 0) {
        out->notes |= MOY_THEME_N_UNKNOWN;
        return 0;
    }
    if (bad || eff < 0 || (eff > 0 && (role < 0 || moy_theme_flag[role]))) {
        out->notes |= MOY_THEME_N_DROPPED;
        return 0;
    }
    for (int i = 0; i < out->n_effects; i++) {
        if (out->effect[i][0] == kind && out->effect[i][1] == state) {
            out->effect[i][2] = (uint8_t)eff;
            out->effect[i][3] = eff > 0 ? (uint8_t)role : MOY_THEME_NO_ROLE;
            return 0;
        }
    }
    if (out->n_effects >= MOY_THEME_EFFECTS_CAP) {
        out->notes |= MOY_THEME_N_DROPPED;
        return 0;
    }
    uint8_t *e = out->effect[out->n_effects++];
    e[0] = (uint8_t)kind;
    e[1] = (uint8_t)state;
    e[2] = (uint8_t)eff;
    e[3] = eff > 0 ? (uint8_t)role : MOY_THEME_NO_ROLE;
    return 0;
}

// An object whose keys are names in `names` (-1 for another), each value an
// object handed to `each` with that index and `arg`; any other value is
// dropped. 0, or -1: malformed.
typedef int (*each_fn)(rd_t *r, moy_theme_pkg_t *out, int i, int arg);

static int keyed(rd_t *r, moy_theme_pkg_t *out, const char *const *names, int count,
                 each_fn each, int arg) {
    if (eat(r, '}')) {
        return 0;
    }
    r->depth++;
    for (;;) {
        char key[MOY_THEME_NAME_MAX + 1];
        int over;
        int32_t iv;
        if (str(r, key, sizeof(key), &over) < 0 || !eat(r, ':')) {
            return -1;
        }
        int i = over ? -1 : find_name(names, count, key);
        if (eat(r, '{')) {
            if (++r->depth > JSON_DEPTH || each(r, out, i, arg) < 0) {
                return -1;
            }
            r->depth--;
        } else {
            if (value(r, &iv) < 0) {
                return -1;
            }
            out->notes |= MOY_THEME_N_DROPPED;
        }
        if (eat(r, ',')) {
            continue;
        }
        r->depth--;
        return eat(r, '}') ? 0 : -1;
    }
}

static int each_state(rd_t *r, moy_theme_pkg_t *out, int state, int kind) {
    return effect(r, out, kind, state);
}

static int each_part(rd_t *r, moy_theme_pkg_t *out, int kind, int unused) {
    (void)unused;
    return keyed(r, out, moy_ui_state_names, MOY_UI_STATES, each_state, kind);
}

static int reserved(const char *key) {
    for (int i = 0; i < MOY_THEME_RESERVED; i++) {
        if (strcmp(moy_theme_reserved[i], key) == 0) {
            return 1;
        }
    }
    return 0;
}

int moy_theme_parse(const uint8_t *src, size_t n, moy_theme_pkg_t *out) {
    memset(out, 0, sizeof(*out));
    memset(out->role, MOY_THEME_UNSET, sizeof(out->role));
    memset(out->metric, MOY_THEME_NO_METRIC, sizeof(out->metric));
    if (n > MOY_THEME_BYTES) {
        return MOY_THEME_BIG;
    }
    rd_t r = {src, src + n, 1};
    int32_t format = -1;
    int fmt_bad = 0, name_bad = 1, chain_bad = 0;
    if (!eat(&r, '{')) {
        return MOY_THEME_SYNTAX;
    }
    if (!eat(&r, '}')) {
        for (;;) {
            char key[16];
            int over;
            int32_t iv = 0;
            if (str(&r, key, sizeof(key), &over) < 0 || !eat(&r, ':')) {
                return MOY_THEME_SYNTAX;
            }
            if (over) {
                key[0] = 0;
            }
            int kind = 0;
            if (strcmp(key, "format") == 0) {
                kind = value(&r, &iv);
                fmt_bad = kind != V_INT;
                format = iv;
            } else if (strcmp(key, "name") == 0 || strcmp(key, "inherits") == 0) {
                int is_name = key[0] == 'n';
                char *dst = is_name ? out->name : out->inherits;
                int len = peek(&r) == '"'
                    ? str(&r, dst, MOY_THEME_NAME_MAX + 1, &over) : -2;
                if (len == -2) {
                    kind = value(&r, &iv);
                } else if (len < 0) {
                    kind = -1;
                }
                int ok = len >= 0 && name_ok(dst, len, over);
                if (!ok) {
                    dst[0] = 0;
                }
                if (is_name) {
                    name_bad = !ok;
                } else {
                    chain_bad = !ok;
                }
            } else if (strcmp(key, "colors") == 0 && eat(&r, '{')) {
                kind = colors(&r, out);
            } else if (strcmp(key, "metrics") == 0 && eat(&r, '{')) {
                kind = metrics(&r, out);
            } else if (strcmp(key, "text") == 0 && eat(&r, '{')) {
                kind = keyed(&r, out, moy_ui_kind_names, MOY_UI_KINDS, each_part, 0);
            } else {
                if (strcmp(key, "colors") == 0 || strcmp(key, "metrics") == 0
                    || strcmp(key, "text") == 0) {
                    out->notes |= MOY_THEME_N_DROPPED;
                } else if (!reserved(key)) {
                    out->notes |= MOY_THEME_N_UNKNOWN;
                }
                kind = value(&r, &iv);
            }
            if (kind < 0) {
                return MOY_THEME_SYNTAX;
            }
            if (eat(&r, ',')) {
                continue;
            }
            if (eat(&r, '}')) {
                break;
            }
            return MOY_THEME_SYNTAX;
        }
    }
    if (peek(&r) != -1) {
        return MOY_THEME_SYNTAX;
    }
    if (fmt_bad || format < 1 || format > MOY_THEME_FORMAT) {
        return MOY_THEME_NEWER;
    }
    if (name_bad) {
        return MOY_THEME_NAME;
    }
    if (chain_bad) {
        return MOY_THEME_CHAIN;
    }
    return MOY_THEME_OK;
}

// -- the seeds and the one installed package ------------------------------------

static moy_theme_pkg_t *s_slot;     // allocated at the first install
static int s_have;

int moy_theme_count(void) {
    return MOY_THEME_SEEDS;
}

const char *moy_theme_name(int i) {
    return i >= 0 && i < MOY_THEME_SEEDS ? moy_theme_seed_names[i] : NULL;
}

const char *moy_theme_installed(void) {
    return s_have ? s_slot->name : NULL;
}

static int seed_of(const char *name, size_t n) {
    for (int i = 0; i < MOY_THEME_SEEDS; i++) {
        if (strlen(moy_theme_seed_names[i]) == n
            && memcmp(moy_theme_seed_names[i], name, n) == 0) {
            return i;
        }
    }
    return -1;
}

// The package called `name` into *out: the installed one or a seed. 0, or -1.
static int load(const char *name, size_t n, moy_theme_pkg_t *out) {
    if (s_have && strlen(s_slot->name) == n && memcmp(s_slot->name, name, n) == 0) {
        memcpy(out, s_slot, sizeof(*out));
        return 0;
    }
    int s = seed_of(name, n);
    if (s < 0) {
        return -1;
    }
    return moy_theme_parse((const uint8_t *)moy_theme_seed_src[s],
                           moy_theme_seed_len[s], out) == MOY_THEME_OK ? 0 : -1;
}

static int known(const char *name, size_t n) {
    return seed_of(name, n) >= 0
           || (s_have && strlen(s_slot->name) == n && memcmp(s_slot->name, name, n) == 0);
}

static void derive(int32_t row[MOY_THEME_ROLES], int at) {
    const uint8_t *span = moy_theme_rules_at[at];
    for (int i = span[0]; i < span[0] + span[1]; i++) {
        const moy_theme_rule_t *u = &moy_theme_rules[i];
        if (row[u->role] == UNSET_ROW) {
            row[u->role] = u->is_role ? row[u->v] : u->v;
        }
    }
}

// Walk `name`'s chain, nearest package first, handing each to `apply`.
typedef void (*apply_fn)(const moy_theme_pkg_t *pkg, void *ctx);

static int chain(const char *name, size_t n, apply_fn apply, void *ctx) {
    char seen[MOY_THEME_DEPTH + 1][MOY_THEME_NAME_MAX + 1];
    moy_theme_pkg_t pkg;
    const char *def = moy_theme_seed_names[0];
    char cur[MOY_THEME_NAME_MAX + 1];
    if (!known(name, n) || n > MOY_THEME_NAME_MAX) {
        name = def;
        n = strlen(def);
    }
    memcpy(cur, name, n);
    cur[n] = 0;
    for (int depth = 0;; depth++) {
        if (depth > MOY_THEME_DEPTH) {
            return MOY_THEME_CHAIN;
        }
        for (int i = 0; i < depth; i++) {
            if (strcmp(seen[i], cur) == 0) {
                return MOY_THEME_CHAIN;
            }
        }
        strcpy(seen[depth], cur);
        if (load(cur, strlen(cur), &pkg) < 0) {
            return MOY_THEME_CHAIN;
        }
        apply(&pkg, ctx);
        if (pkg.inherits[0]) {
            strcpy(cur, pkg.inherits);
        } else if (strcmp(cur, def) != 0) {
            strcpy(cur, def);
        } else {
            return MOY_THEME_OK;
        }
    }
}

typedef struct {
    int variant;
    int32_t *row;
} row_ctx_t;

static void apply_row(const moy_theme_pkg_t *pkg, void *ctx) {
    row_ctx_t *c = ctx;
    for (int i = 0; i < MOY_THEME_ROLES; i++) {
        if (c->row[i] == UNSET_ROW && pkg->role[c->variant][i] != MOY_THEME_UNSET) {
            c->row[i] = pkg->role[c->variant][i];
        }
    }
}

static int resolve_row(const char *name, size_t n, int variant, int32_t row[MOY_THEME_ROLES]) {
    row_ctx_t c = {variant, row};
    for (int i = 0; i < MOY_THEME_ROLES; i++) {
        row[i] = UNSET_ROW;
    }
    int rc = chain(name, n, apply_row, &c);
    if (rc == MOY_THEME_OK) {
        derive(row, variant);
        derive(row, 2);
    }
    return rc;
}

int moy_theme_resolve(const char *name, size_t n, int variant,
                      int32_t row[MOY_THEME_ROLES], uint32_t *unreadable) {
    variant = variant == MOY_THEME_LIGHT ? MOY_THEME_LIGHT : MOY_THEME_DARK;
    int rc = resolve_row(name, n, variant, row);
    if (rc != MOY_THEME_OK) {
        const char *def = moy_theme_seed_names[0];
        resolve_row(def, strlen(def), variant, row);
    }
    uint32_t bad = 0;
    for (int i = 0; i < MOY_THEME_PAIRS; i++) {
        int32_t a = row[moy_theme_pairs[i][0]], b = row[moy_theme_pairs[i][1]];
        if (a < 0 || a > 63 || b < 0 || b > 63) {
            continue;
        }
        int32_t la = moy_theme_lum[a], lb = moy_theme_lum[b];
        int32_t hi = la > lb ? la : lb, lo = la > lb ? lb : la;
        if ((hi + 500) * 10 < MOY_THEME_CONTRAST * (lo + 500)) {
            bad |= 1u << i;
        }
    }
    if (unreadable != NULL) {
        *unreadable = bad;
    }
    return rc;
}

typedef struct {
    int8_t *metrics;
    uint8_t (*effects)[MOY_UI_STATES][2];
    uint8_t have[MOY_UI_KINDS][MOY_UI_STATES];
} shape_ctx_t;

static void apply_shape(const moy_theme_pkg_t *pkg, void *ctx) {
    shape_ctx_t *c = ctx;
    for (int i = 0; i < MOY_THEME_METRICS; i++) {
        if (c->metrics[i] == MOY_THEME_NO_METRIC) {
            c->metrics[i] = pkg->metric[i];
        }
    }
    for (int i = 0; i < pkg->n_effects; i++) {
        const uint8_t *e = pkg->effect[i];
        if (!c->have[e[0]][e[1]]) {
            c->have[e[0]][e[1]] = 1;
            c->effects[e[0]][e[1]][0] = e[2];
            c->effects[e[0]][e[1]][1] = e[3];
        }
    }
}

static int resolve_shape(const char *name, size_t n, int8_t metrics[MOY_THEME_METRICS],
                         uint8_t effects[MOY_UI_KINDS][MOY_UI_STATES][2]) {
    shape_ctx_t c;
    memset(&c, 0, sizeof(c));
    c.metrics = metrics;
    c.effects = effects;
    memset(metrics, MOY_THEME_NO_METRIC, MOY_THEME_METRICS);
    for (int k = 0; k < MOY_UI_KINDS; k++) {
        for (int st = 0; st < MOY_UI_STATES; st++) {
            effects[k][st][0] = 0;
            effects[k][st][1] = MOY_THEME_NO_ROLE;
        }
    }
    return chain(name, n, apply_shape, &c);
}

int moy_theme_shape(const char *name, size_t n, int8_t metrics[MOY_THEME_METRICS],
                    uint8_t effects[MOY_UI_KINDS][MOY_UI_STATES][2]) {
    int rc = resolve_shape(name, n, metrics, effects);
    if (rc != MOY_THEME_OK) {
        const char *def = moy_theme_seed_names[0];
        resolve_shape(def, strlen(def), metrics, effects);
    }
    return rc;
}

int moy_theme_install(const uint8_t *src, size_t n, uint8_t *notes) {
    moy_theme_pkg_t pkg;
    int rc = moy_theme_parse(src, n, &pkg);
    if (notes != NULL) {
        *notes = pkg.notes;
    }
    if (rc != MOY_THEME_OK) {
        return rc;
    }
    if (seed_of(pkg.name, strlen(pkg.name)) >= 0) {
        return MOY_THEME_TAKEN;
    }
    if (s_slot == NULL) {
        s_slot = THEME_ALLOC(sizeof(*s_slot));
        if (s_slot == NULL) {
            return MOY_THEME_NOMEM;
        }
    }
    moy_theme_pkg_t was;
    int had = s_have;
    memcpy(&was, s_slot, sizeof(was));
    memcpy(s_slot, &pkg, sizeof(pkg));
    s_have = 1;
    int32_t row[MOY_THEME_ROLES];
    if (resolve_row(pkg.name, strlen(pkg.name), MOY_THEME_DARK, row) != MOY_THEME_OK) {
        memcpy(s_slot, &was, sizeof(was));
        s_have = had;
        return MOY_THEME_CHAIN;
    }
    return MOY_THEME_OK;
}

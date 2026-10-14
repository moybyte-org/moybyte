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
#define FX_UNSET 0xfe      // an effect no package has named yet

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

// `buttons`: each letter of MOY_THEME_FRAME_BUTTONS once, a '|' between them
// allowed, into out[0..3]. 1, or 0 for any other text.
static int button_order(const char *s, int n, uint8_t out[4]) {
    const char *set = MOY_THEME_FRAME_BUTTONS;
    int k = 0;
    for (int i = 0; i < n; i++) {
        if (s[i] == '|') {
            continue;
        }
        const char *at = strchr(set, s[i]);
        if (s[i] == 0 || at == NULL || k == 4 || memchr(out, s[i], (size_t)k) != NULL) {
            return 0;
        }
        out[k++] = (uint8_t)s[i];
    }
    return k == 4;
}

// `frame`: {"style", "title_align", "buttons"} into out->frame (moy_ui.h's
// layout). 0, or -1: malformed.
static int frame_key(rd_t *r, moy_theme_pkg_t *out) {
    if (eat(r, '}')) {
        return 0;
    }
    r->depth++;
    for (;;) {
        char key[16];
        int over;
        if (str(r, key, sizeof(key), &over) < 0 || !eat(r, ':')) {
            return -1;
        }
        int is_style = !over && strcmp(key, "style") == 0;
        int is_align = !over && strcmp(key, "title_align") == 0;
        if (is_style || is_align) {
            int i = is_style ? name_field(r, moy_theme_frame_names, MOY_THEME_FRAMES)
                             : name_field(r, moy_theme_align_names, MOY_THEME_ALIGNS);
            if (i == -1) {
                return -1;
            }
            if (i < 0) {
                out->notes |= MOY_THEME_N_DROPPED;
            } else {
                out->frame[is_style ? 0 : 1] = (uint8_t)(i + 1);
            }
        } else if (!over && strcmp(key, "buttons") == 0) {
            char buf[16];
            int32_t iv;
            int len = peek(r) == '"' ? str(r, buf, sizeof(buf), &over) : -2;
            if (len == -1 || (len == -2 && value(r, &iv) < 0)) {
                return -1;
            }
            uint8_t order[4];
            if (len >= 0 && !over && button_order(buf, len, order)) {
                memcpy(&out->frame[2], order, 4);
            } else {
                out->notes |= MOY_THEME_N_DROPPED;
            }
        } else {
            int32_t iv;
            if (value(r, &iv) < 0) {
                return -1;
            }
            out->notes |= MOY_THEME_N_UNKNOWN;
        }
        if (eat(r, ',')) {
            continue;
        }
        r->depth--;
        return eat(r, '}') ? 0 : -1;
    }
}

// A font file's name: 1 to MOY_THEME_NAME_MAX of [a-z0-9_.-], not starting with '.'.
static int file_ok(const char *s, int n, int over) {
    if (n < 1 || over || n > MOY_THEME_NAME_MAX || s[0] == '.') {
        return 0;
    }
    for (int i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-'
              || c == '.')) {
            return 0;
        }
    }
    return 1;
}

// `fonts`: role -> a font file of the package. 0, or -1: malformed.
static int fonts_key(rd_t *r, moy_theme_pkg_t *out) {
    if (eat(r, '}')) {
        return 0;
    }
    r->depth++;
    for (;;) {
        char key[16];
        char file[MOY_THEME_NAME_MAX + 1];
        int over;
        int32_t iv;
        if (str(r, key, sizeof(key), &over) < 0 || !eat(r, ':')) {
            return -1;
        }
        int role = over ? -1 : find_name(moy_theme_font_roles, MOY_THEME_FONT_ROLES, key);
        int len = peek(r) == '"' ? str(r, file, sizeof(file), &over) : -2;
        if (len == -1 || (len == -2 && value(r, &iv) < 0)) {
            return -1;
        }
        if (role < 0) {
            out->notes |= MOY_THEME_N_UNKNOWN;
        } else if (len >= 0 && file_ok(file, len, over)) {
            memcpy(out->font[role], file, (size_t)len);
            out->font[role][len] = 0;
        } else {
            out->notes |= MOY_THEME_N_DROPPED;
        }
        if (eat(r, ',')) {
            continue;
        }
        r->depth--;
        return eat(r, '}') ? 0 : -1;
    }
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

static int each_part(rd_t *r, moy_theme_pkg_t *out, int part, int unused) {
    (void)unused;
    return keyed(r, out, moy_theme_state_names, MOY_THEME_STATES, each_state, part);
}

// -- surfaces: the grammar (docs/theming_2026-09.md §4.4) ------------------------
//
//   surface := clause { "," clause }
//   clause  := EDGE | "fill" C | ("dither" | "pinstripe" | "gradient") C "/" C
//            | "border" [C] | "shadow"
//   C       := a token of the live row (not a flag) | a palette index
//
// Words are lower case, split by spaces; each clause at most once. What is
// left out is the part's own: no edge is flat, no fill its field, a bare
// border its edge colour.

static int is_space(char c) {
    return c == ' ' || c == '\t';
}

// The next word of [*p, end) up to a space or a comma: its length (0: none).
static int next_word(const char **p, const char *end, const char **w) {
    while (*p < end && is_space(**p)) {
        (*p)++;
    }
    *w = *p;
    while (*p < end && !is_space(**p) && **p != ',') {
        (*p)++;
    }
    return (int)(*p - *w);
}

static int word_is(const char *w, int n, const char *lit) {
    return (int)strlen(lit) == n && memcmp(w, lit, (size_t)n) == 0;
}

static int word_in(const char *w, int n, const char *const *names, int count) {
    for (int i = 0; i < count; i++) {
        if (word_is(w, n, names[i])) {
            return i;
        }
    }
    return -1;
}

// A colour word: a token's term or an index. 0, or -1.
static int term_of(const char *w, int n, int8_t *out, uint8_t *notes) {
    if (n >= 1 && w[0] >= '0' && w[0] <= '9') {
        if (n > 3) {
            return -1;
        }
        int v = 0;
        for (int i = 0; i < n; i++) {
            if (w[i] < '0' || w[i] > '9') {
                return -1;
            }
            v = v * 10 + (w[i] - '0');
        }
        if (v > 63) {
            v = 63;
            *notes |= MOY_THEME_N_CLAMPED;
        }
        *out = (int8_t)v;
        return 0;
    }
    int t = word_in(w, n, moy_ui_token_names, MOY_UI_TOKENS);
    if (t < 0 || moy_theme_flag[t]) {
        return -1;
    }
    *out = MOY_UI_TERM_TOKEN(t);
    return 0;
}

int moy_theme_surface(const char *s, size_t n, moy_ui_surface_t *out, uint8_t *notes) {
    const char *p = s, *end = s + n;
    uint8_t seen = 0, note = 0;     // bits: edge, fill, border, shadow
    memset(out, 0, sizeof(*out));
    out->a = out->b = out->line = MOY_UI_TERM_STYLE;
    if (n == 0 || n > MOY_THEME_SURFACE_LEN) {
        return -1;
    }
    for (;;) {
        const char *w;
        int wn = next_word(&p, end, &w);
        if (wn == 0) {
            return -1;              // an empty clause
        }
        int i;
        uint8_t bit;
        if ((i = word_in(w, wn, moy_theme_edge_names, MOY_THEME_EDGES)) >= 0) {
            bit = 1;
            out->edge = (uint8_t)i;
        } else if ((i = word_in(w, wn, moy_theme_fill_names, MOY_THEME_FILLS)) >= 0) {
            bit = 2;
            out->fill = (uint8_t)(i + 1);
            const char *c;
            int cn = next_word(&p, end, &c);
            if (out->fill == MOY_UI_FILL_SOLID) {
                if (term_of(c, cn, &out->a, &note) < 0) {
                    return -1;
                }
            } else {
                const char *slash = memchr(c, '/', (size_t)cn);
                if (slash == NULL || term_of(c, (int)(slash - c), &out->a, &note) < 0
                    || term_of(slash + 1, cn - (int)(slash - c) - 1, &out->b, &note) < 0) {
                    return -1;
                }
            }
        } else if (word_is(w, wn, "border")) {
            bit = 4;
            out->flags |= MOY_UI_SURF_BORDER;
            const char *c;
            int cn = next_word(&p, end, &c);
            if (cn > 0 && term_of(c, cn, &out->line, &note) < 0) {
                return -1;
            }
        } else if (word_is(w, wn, "shadow")) {
            bit = 8;
            out->flags |= MOY_UI_SURF_SHADOW;
        } else {
            return -1;
        }
        if (seen & bit) {
            return -1;
        }
        seen |= bit;
        const char *rest;
        if (next_word(&p, end, &rest) != 0) {
            return -1;              // a word past the clause's end
        }
        if (p == end) {
            break;
        }
        p++;                        // the comma
    }
    *notes |= note;
    return MOY_THEME_OK;
}

// One part's surfaces: an object of state -> text. 0, or -1: malformed.
static int each_surface(rd_t *r, moy_theme_pkg_t *out, int part, int unused) {
    (void)unused;
    if (eat(r, '}')) {
        return 0;
    }
    for (;;) {
        char key[MOY_THEME_NAME_MAX + 1];
        char text[MOY_THEME_SURFACE_LEN + 1];
        int over, tover = 0, len = -2;
        int32_t iv;
        if (str(r, key, sizeof(key), &over) < 0 || !eat(r, ':')) {
            return -1;
        }
        int state = over ? -1 : find_name(moy_theme_state_names, MOY_THEME_STATES, key);
        if (peek(r) == '"') {
            len = str(r, text, sizeof(text), &tover);
            if (len < 0) {
                return -1;
            }
        } else if (value(r, &iv) < 0) {
            return -1;
        }
        moy_ui_surface_t rec;
        if (part < 0 || state < 0) {
            out->notes |= MOY_THEME_N_UNKNOWN;
        } else if (len < 0 || tover || moy_theme_surface(text, (size_t)len, &rec, &out->notes)) {
            out->notes |= MOY_THEME_N_DROPPED;
        } else {
            int i = 0;
            while (i < out->n_surfaces
                   && !(out->surface[i][0] == part && out->surface[i][1] == state)) {
                i++;
            }
            if (i == out->n_surfaces && out->n_surfaces >= MOY_THEME_SURFACES_CAP) {
                out->notes |= MOY_THEME_N_DROPPED;
            } else {
                if (i == out->n_surfaces) {
                    out->n_surfaces++;
                }
                out->surface[i][0] = (uint8_t)part;
                out->surface[i][1] = (uint8_t)state;
                out->surface_rec[i] = rec;
            }
        }
        if (eat(r, ',')) {
            continue;
        }
        return eat(r, '}') ? 0 : -1;
    }
}

// -- the layout (docs/shell_layout_2026-10.md §5, §9) ------------------------------

// One bar as written, before the layout's caps: its strings by position, ""
// where one was dropped, so the strings after it keep their widgets.
typedef struct {
    int edge;              // index of moy_theme_bar_edges, or -1: none it takes
    int size;
    uint8_t n[MOY_THEME_SLOTS];
    uint8_t w[MOY_THEME_SLOTS][MOY_THEME_SLOT_CAP];
    uint8_t n_text;
    char text[MOY_THEME_WIDGET_CAP][MOY_THEME_TEXT_MAX + 1];
} raw_bar_t;

// A slot's list of widget names, at most MOY_THEME_SLOT_CAP. 0, or -1: malformed.
static int slot_list(rd_t *r, moy_theme_pkg_t *out, raw_bar_t *b, int slot) {
    b->n[slot] = 0;
    if (eat(r, ']')) {
        return 0;
    }
    r->depth++;
    for (;;) {
        int i = name_field(r, moy_theme_widget_names, MOY_THEME_WIDGETS);
        if (i == -1) {
            return -1;
        }
        if (i == -2) {
            out->notes |= MOY_THEME_N_UNKNOWN;
        } else if (i < 0 || b->n[slot] == MOY_THEME_SLOT_CAP) {
            out->notes |= MOY_THEME_N_DROPPED;
        } else {
            b->w[slot][b->n[slot]++] = (uint8_t)i;
        }
        if (eat(r, ',')) {
            continue;
        }
        r->depth--;
        return eat(r, ']') ? 0 : -1;
    }
}

// A bar's `text`: its strings, 1 to MOY_THEME_TEXT_MAX of printable ASCII,
// longer cut. 0, or -1: malformed.
static int text_list(rd_t *r, moy_theme_pkg_t *out, raw_bar_t *b) {
    b->n_text = 0;
    if (eat(r, ']')) {
        return 0;
    }
    r->depth++;
    for (;;) {
        char buf[MOY_THEME_TEXT_MAX + 1];
        int over;
        int32_t iv;
        int len = peek(r) == '"' ? str(r, buf, sizeof(buf), &over) : -2;
        if (len == -1 || (len == -2 && value(r, &iv) < 0)) {
            return -1;
        }
        int ok = len > 0;
        for (int i = 0; ok && i < len && i < MOY_THEME_TEXT_MAX; i++) {
            ok = buf[i] >= 0x20 && buf[i] < 0x7f;
        }
        if (!ok) {
            buf[0] = 0;
            out->notes |= MOY_THEME_N_DROPPED;
        } else if (over) {
            out->notes |= MOY_THEME_N_CLAMPED;
        }
        if (b->n_text == MOY_THEME_WIDGET_CAP) {
            out->notes |= MOY_THEME_N_DROPPED;
        } else {
            memcpy(b->text[b->n_text++], buf, sizeof(buf));
        }
        if (eat(r, ',')) {
            continue;
        }
        r->depth--;
        return eat(r, ']') ? 0 : -1;
    }
}

// One bar object into *b. 0, or -1: malformed.
static int bar_obj(rd_t *r, moy_theme_pkg_t *out, raw_bar_t *b) {
    memset(b, 0, sizeof(*b));
    b->edge = -1;
    b->size = MOY_THEME_BAR_SIZE;
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
        int slot = over ? -1 : find_name(moy_theme_slot_names, MOY_THEME_SLOTS, key);
        int rc = 0;
        if (!over && strcmp(key, "edge") == 0) {
            int i = name_field(r, moy_theme_bar_edges, MOY_THEME_BARS);
            if (i == -1) {
                return -1;
            }
            b->edge = i < 0 ? -1 : i;
        } else if (!over && strcmp(key, "size") == 0) {
            int kind = value(r, &iv);
            if (kind < 0) {
                return -1;
            }
            if (kind != V_INT) {
                out->notes |= MOY_THEME_N_DROPPED;
            } else {
                int v = iv < MOY_THEME_BAR_MIN ? MOY_THEME_BAR_MIN
                        : iv > MOY_THEME_BAR_MAX ? MOY_THEME_BAR_MAX : (int)iv;
                if (v != iv) {
                    out->notes |= MOY_THEME_N_CLAMPED;
                }
                b->size = v;
            }
        } else if (slot >= 0 && eat(r, '[')) {
            rc = slot_list(r, out, b, slot);
        } else if (!over && strcmp(key, "text") == 0 && eat(r, '[')) {
            rc = text_list(r, out, b);
        } else {
            rc = value(r, &iv);
            out->notes |= (slot >= 0 || (!over && strcmp(key, "text") == 0))
                          ? MOY_THEME_N_DROPPED : MOY_THEME_N_UNKNOWN;
        }
        if (rc < 0) {
            return -1;
        }
        if (eat(r, ',')) {
            continue;
        }
        r->depth--;
        return eat(r, '}') ? 0 : -1;
    }
}

// Commit a bar to the layout in placement order: a second bar on an edge is
// dropped; a widget placed twice keeps its first placement (but the
// repeatable ones), the layout holds at most MOY_THEME_WIDGET_CAP, and a
// `text` widget takes its string by position or is dropped. *seen collects
// the placed widgets, a bit each.
static void bar_commit(moy_theme_pkg_t *out, const raw_bar_t *b, uint16_t *seen, int *count) {
    moy_theme_layout_t *lay = &out->layout;
    int taken = b->edge < 0;
    for (int i = 0; i < lay->n_bars; i++) {
        taken |= lay->bar[i].edge == b->edge;
    }
    if (taken) {
        out->notes |= MOY_THEME_N_DROPPED;
        return;
    }
    moy_theme_bar_t *bar = &lay->bar[lay->n_bars++];
    memset(bar, 0, sizeof(*bar));
    bar->edge = (uint8_t)b->edge;
    bar->size = (uint8_t)b->size;
    bar->text0 = lay->n_text;
    int ti = 0;
    for (int s = 0; s < MOY_THEME_SLOTS; s++) {
        for (int k = 0; k < b->n[s]; k++) {
            int wid = b->w[s][k];
            uint16_t bit = (uint16_t)(1u << wid);
            const char *t = NULL;
            if (wid == MOY_THEME_W_TEXT) {
                t = ti < b->n_text ? b->text[ti] : "";
                ti++;
                if (!t[0]) {
                    out->notes |= MOY_THEME_N_DROPPED;
                    continue;
                }
            }
            if ((*seen & bit) && !(MOY_THEME_W_REPEATABLE & bit)) {
                out->notes |= MOY_THEME_N_DROPPED;
                continue;
            }
            if (*count == MOY_THEME_WIDGET_CAP) {
                out->notes |= MOY_THEME_N_DROPPED;
                continue;
            }
            *seen |= bit;
            (*count)++;
            bar->w[s][bar->n[s]++] = (uint8_t)wid;
            if (t != NULL) {
                memcpy(lay->text[lay->n_text++], t, MOY_THEME_TEXT_MAX + 1);
                bar->n_text++;
            }
        }
    }
    if (ti < b->n_text) {
        out->notes |= MOY_THEME_N_DROPPED;     // strings without a `text` widget
    }
}

// `bars`: the list, then the required-set rule on what it placed. 0, or -1.
static int bars_list(rd_t *r, moy_theme_pkg_t *out) {
    moy_theme_layout_t *lay = &out->layout;
    memset(lay, 0, sizeof(*lay));
    lay->set = MOY_THEME_LAYOUT_SET;
    uint16_t seen = 0;
    int count = 0;
    if (!eat(r, ']')) {
        r->depth++;
        for (;;) {
            if (eat(r, '{')) {
                raw_bar_t b;
                if (bar_obj(r, out, &b) < 0) {
                    return -1;
                }
                bar_commit(out, &b, &seen, &count);
            } else {
                int32_t iv;
                if (value(r, &iv) < 0) {
                    return -1;
                }
                out->notes |= MOY_THEME_N_DROPPED;
            }
            if (eat(r, ',')) {
                continue;
            }
            r->depth--;
            if (!eat(r, ']')) {
                return -1;
            }
            break;
        }
    }
    if ((seen & MOY_THEME_W_REQUIRED) != MOY_THEME_W_REQUIRED) {
        lay->set = MOY_THEME_LAYOUT_REFUSED;
        out->notes |= MOY_THEME_N_DROPPED;
    }
    return 0;
}

// `layout`: {"bars": [...], "home": "<arrangement>"}. The home rides the
// layout, which a package sets whole with its bars: an unknown arrangement
// is noted and the first, the default, stands. 0, or -1: malformed.
static int layout_key(rd_t *r, moy_theme_pkg_t *out) {
    if (eat(r, '}')) {
        return 0;
    }
    int home = 0;
    r->depth++;
    for (;;) {
        char key[16];
        int over;
        int32_t iv;
        if (str(r, key, sizeof(key), &over) < 0 || !eat(r, ':')) {
            return -1;
        }
        int is_bars = !over && strcmp(key, "bars") == 0;
        int is_home = !over && strcmp(key, "home") == 0;
        int rc;
        if (is_bars && eat(r, '[')) {
            rc = bars_list(r, out);
        } else if (is_home && peek(r) == '"') {
            char name[16];
            rc = str(r, name, sizeof(name), &over);
            int h = over ? -1 : find_name(moy_theme_home_names, MOY_THEME_HOMES, name);
            if (h < 0) {
                out->notes |= MOY_THEME_N_UNKNOWN;
            } else {
                home = h;
            }
        } else {
            rc = value(r, &iv);
            out->notes |= (is_bars || is_home) ? MOY_THEME_N_DROPPED : MOY_THEME_N_UNKNOWN;
        }
        if (rc < 0) {
            return -1;
        }
        if (eat(r, ',')) {
            continue;
        }
        r->depth--;
        out->layout.home = (uint8_t)home;
        return eat(r, '}') ? 0 : -1;
    }
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
                kind = keyed(&r, out, moy_theme_part_names, MOY_THEME_PARTS, each_part, 0);
            } else if (strcmp(key, "surfaces") == 0 && eat(&r, '{')) {
                kind = keyed(&r, out, moy_theme_part_names, MOY_THEME_PARTS, each_surface, 0);
            } else if (strcmp(key, "frame") == 0 && eat(&r, '{')) {
                kind = frame_key(&r, out);
            } else if (strcmp(key, "fonts") == 0 && eat(&r, '{')) {
                kind = fonts_key(&r, out);
            } else if (strcmp(key, "layout") == 0 && eat(&r, '{')) {
                kind = layout_key(&r, out);
            } else {
                if (strcmp(key, "colors") == 0 || strcmp(key, "metrics") == 0
                    || strcmp(key, "text") == 0 || strcmp(key, "surfaces") == 0
                    || strcmp(key, "frame") == 0 || strcmp(key, "fonts") == 0
                    || strcmp(key, "layout") == 0) {
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

void moy_theme_uninstall(void) {
    s_have = 0;
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

static void apply_shape(const moy_theme_pkg_t *pkg, void *ctx) {
    moy_ui_shape_t *sh = ctx;
    for (int i = 0; i < 2; i++) {
        if (sh->frame[i] == 0) {
            sh->frame[i] = pkg->frame[i];
        }
    }
    if (sh->frame[2] == 0) {
        memcpy(&sh->frame[2], &pkg->frame[2], 4);
    }
    for (int i = 0; i < MOY_THEME_METRICS; i++) {
        if (sh->metric[i] == MOY_THEME_NO_METRIC) {
            sh->metric[i] = pkg->metric[i];
        }
    }
    for (int i = 0; i < pkg->n_effects; i++) {
        const uint8_t *e = pkg->effect[i];
        uint8_t *at = sh->effect[e[0]][e[1]];
        if (at[1] == FX_UNSET) {
            at[0] = e[2];
            at[1] = e[2] != 0 ? e[3] : MOY_UI_FX_NAMED_NONE;
            if (e[2] != 0) {
                sh->n_fx++;
            }
        }
    }
    for (int i = 0; i < pkg->n_surfaces; i++) {
        const uint8_t *ps = pkg->surface[i];
        if (sh->stid[ps[0]][ps[1]] != 0) {
            continue;
        }
        const moy_ui_surface_t *rec = &pkg->surface_rec[i];
        int k = 0;
        while (k < sh->n_surf && memcmp(&sh->surf[k], rec, sizeof(*rec)) != 0) {
            k++;
        }
        if (k == sh->n_surf) {
            if (sh->n_surf >= MOY_UI_STIDS) {
                continue;
            }
            sh->surf[sh->n_surf++] = *rec;
        }
        sh->stid[ps[0]][ps[1]] = (uint8_t)(k + 1);
    }
}

static int resolve_shape(const char *name, size_t n, moy_ui_shape_t *sh) {
    moy_ui_shape_clear(sh);
    memset(sh->metric, MOY_THEME_NO_METRIC, sizeof(sh->metric));
    for (int p = 0; p < MOY_THEME_PARTS; p++) {
        for (int st = 0; st < MOY_THEME_STATES; st++) {
            sh->effect[p][st][1] = FX_UNSET;
        }
    }
    int rc = chain(name, n, apply_shape, sh);
    for (int p = 0; p < MOY_THEME_PARTS; p++) {
        for (int st = 0; st < MOY_THEME_STATES; st++) {
            if (sh->effect[p][st][1] == FX_UNSET) {
                sh->effect[p][st][0] = 0;
                sh->effect[p][st][1] = MOY_THEME_NO_ROLE;
            }
        }
    }
    return rc;
}

int moy_theme_shape(const char *name, size_t n, moy_ui_shape_t *out) {
    int rc = resolve_shape(name, n, out);
    if (rc != MOY_THEME_OK) {
        name = moy_theme_seed_names[0];
        n = strlen(name);
        resolve_shape(name, n, out);
    }
    n = n < MOY_UI_SHAPE_NAME - 1 ? n : MOY_UI_SHAPE_NAME - 1;
    memcpy(out->name, name, n);
    out->name[n] = 0;
    return rc;
}

static void apply_fonts(const moy_theme_pkg_t *pkg, void *ctx) {
    char (*out)[MOY_THEME_NAME_MAX + 1] = ctx;
    for (int i = 0; i < MOY_THEME_FONT_ROLES; i++) {
        if (out[i][0] == 0) {
            memcpy(out[i], pkg->font[i], sizeof(pkg->font[i]));
        }
    }
}

const uint8_t *moy_theme_font_file(const char *name, size_t n, size_t *len) {
    for (int i = 0; i < MOY_THEME_FONT_FILES; i++) {
        if (strlen(moy_theme_font_names[i]) == n && memcmp(moy_theme_font_names[i], name, n) == 0) {
            *len = moy_theme_font_len[i];
            return moy_theme_font_data[i];
        }
    }
    *len = 0;
    return NULL;
}

int moy_theme_fonts(const char *name, size_t n,
                    char out[MOY_THEME_FONT_ROLES][MOY_THEME_NAME_MAX + 1]) {
    memset(out, 0, MOY_THEME_FONT_ROLES * (MOY_THEME_NAME_MAX + 1));
    int rc = chain(name, n, apply_fonts, out);
    if (rc != MOY_THEME_OK) {
        const char *def = moy_theme_seed_names[0];
        memset(out, 0, MOY_THEME_FONT_ROLES * (MOY_THEME_NAME_MAX + 1));
        chain(def, strlen(def), apply_fonts, out);
    }
    return rc;
}

typedef struct {
    moy_theme_layout_t *out;
    int found;
} layout_ctx_t;

static void apply_layout(const moy_theme_pkg_t *pkg, void *ctx) {
    layout_ctx_t *c = ctx;
    if (!c->found && pkg->layout.set != MOY_THEME_LAYOUT_SILENT) {
        memcpy(c->out, &pkg->layout, sizeof(pkg->layout));
        c->found = 1;
    }
}

const int moy_theme_layout_dims[7] = {
    MOY_THEME_BARS, MOY_THEME_SLOTS, MOY_THEME_SLOT_CAP, MOY_THEME_WIDGET_CAP,
    MOY_THEME_TEXT_MAX, MOY_THEME_WIDGETS, (int)sizeof(moy_theme_layout_t),
};

int moy_theme_layout(const char *name, size_t n, moy_theme_layout_t *out) {
    layout_ctx_t c = {out, 0};
    memset(out, 0, sizeof(*out));
    int rc = chain(name, n, apply_layout, &c);
    if (rc != MOY_THEME_OK || out->set == MOY_THEME_LAYOUT_REFUSED) {
        const char *def = moy_theme_seed_names[0];
        memset(out, 0, sizeof(*out));
        c.found = 0;
        chain(def, strlen(def), apply_layout, &c);
        if (out->set == MOY_THEME_LAYOUT_SET) {
            out->set = MOY_THEME_LAYOUT_REFUSED;
        } else {
            memset(out, 0, sizeof(*out));
        }
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

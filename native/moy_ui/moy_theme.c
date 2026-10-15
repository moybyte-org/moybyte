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

// A theme's name: 1 to MOY_THEME_NAME_MAX of [a-z0-9_-], starting with a letter or
// a digit (the carts repository's theme ids, so a console's theme can be published).
static int name_ok(const char *s, int n, int over) {
    if (n < 1 || over || n > MOY_THEME_NAME_MAX || s[0] == '_' || s[0] == '-') {
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

// A sheet key's value: a file of the package named `X.moygfx`. Anything
// else is dropped. 0, or -1: malformed.
static int sheet_key(rd_t *r, moy_theme_pkg_t *out, int key) {
    char file[MOY_THEME_NAME_MAX + 1];
    int over;
    int32_t iv;
    int len = peek(r) == '"' ? str(r, file, sizeof(file), &over) : -2;
    if (len == -1 || (len == -2 && value(r, &iv) < 0)) {
        return -1;
    }
    static const char k_sfx[] = ".moygfx";
    const int sl = (int)sizeof(k_sfx) - 1;
    if (len > sl && file_ok(file, len, over) && memcmp(file + len - sl, k_sfx, (size_t)sl) == 0) {
        memcpy(out->sheet[key], file, (size_t)len);
        out->sheet[key][len] = 0;
    } else {
        out->notes |= MOY_THEME_N_DROPPED;
    }
    return 0;
}

static int sheet_of(const char *key) {
    for (int i = 0; i < MOY_THEME_SHEETS; i++) {
        if (strcmp(moy_theme_sheet_keys[i], key) == 0) {
            return i;
        }
    }
    return -1;
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
            } else if (sheet_of(key) >= 0) {
                kind = sheet_key(&r, out, sheet_of(key));
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
static const moy_theme_pkg_t *s_probe;  // a package resolved from its bytes, between probe and probe_end

const size_t moy_theme_pkg_bytes = sizeof(moy_theme_pkg_t);

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
static int is_named(const moy_theme_pkg_t *pkg, const char *name, size_t n) {
    return pkg != NULL && strlen(pkg->name) == n && memcmp(pkg->name, name, n) == 0;
}

static int load(const char *name, size_t n, moy_theme_pkg_t *out) {
    if (is_named(s_probe, name, n)) {
        memcpy(out, s_probe, sizeof(*out));
        return 0;
    }
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
    return seed_of(name, n) >= 0 || is_named(s_probe, name, n)
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

static void apply_sheets(const moy_theme_pkg_t *pkg, void *ctx) {
    char (*out)[MOY_THEME_NAME_MAX + 1] = ctx;
    for (int i = 0; i < MOY_THEME_SHEETS; i++) {
        if (out[i][0] == 0) {
            memcpy(out[i], pkg->sheet[i], sizeof(pkg->sheet[i]));
        }
    }
}

int moy_theme_sheets(const char *name, size_t n,
                     char out[MOY_THEME_SHEETS][MOY_THEME_NAME_MAX + 1]) {
    memset(out, 0, MOY_THEME_SHEETS * (MOY_THEME_NAME_MAX + 1));
    int rc = chain(name, n, apply_sheets, out);
    if (rc != MOY_THEME_OK) {
        memset(out, 0, MOY_THEME_SHEETS * (MOY_THEME_NAME_MAX + 1));
    }
    return rc;
}

// -- the sprite sheet -------------------------------------------------------------

const int moy_theme_sheet_dims[6] = {
    MOY_THEME_SHEET_W, MOY_THEME_SPRITES_MAX_ROWS, MOY_THEME_ICONS_MAX_ROWS,
    MOY_THEME_SPRITE_ROWS, MOY_THEME_CELLS, MOY_THEME_SHEETS,
};

static uint8_t *s_sheet;            // MOY_THEME_SHEET_W x MOY_THEME_SPRITE_ROWS slots
static int s_sheet_on;
static uint8_t s_cell_set[MOY_THEME_CELLS];

static int sheet_space(uint8_t c) {
    return c == ' ' || c == '\t' || c == '\r';
}

static int nibble(uint8_t c) {
    return c >= '0' && c <= '9' ? c - '0'
           : c >= 'a' && c <= 'f' ? c - 'a' + 10
           : c >= 'A' && c <= 'F' ? c - 'A' + 10
                                  : -1;
}

// Walk the sheet's rows: each row's digits to `row` (when not NULL, the
// buffer MOY_THEME_SHEET_W wide, rows past `keep` not written).
static int sheet_walk(const uint8_t *src, size_t n, int rows_max, int *rows, uint8_t *row,
                      int keep) {
    if (n > (size_t)MOY_THEME_SHEET_BYTES(rows_max)) {
        return MOY_THEME_BIG;
    }
    const uint8_t *p = src, *end = src + n;
    int y = 0;
    while (p < end) {
        const uint8_t *e = p;
        while (e < end && *e != '\n') {
            e++;
        }
        const uint8_t *a = p, *b = e;
        while (a < b && sheet_space(*a)) {
            a++;
        }
        while (b > a && sheet_space(b[-1])) {
            b--;
        }
        if (b > a) {
            if (b - a > MOY_THEME_SHEET_W) {
                return MOY_THEME_SYNTAX;
            }
            if (y >= rows_max) {
                return MOY_THEME_BIG;
            }
            uint8_t *out = row != NULL && y < keep ? row + (size_t)y * MOY_THEME_SHEET_W : NULL;
            for (const uint8_t *q = a; q < b; q++) {
                int v = nibble(*q);
                if (v < 0) {
                    return MOY_THEME_SYNTAX;
                }
                if (out != NULL) {
                    out[q - a] = (uint8_t)v;
                }
            }
            y++;
        }
        p = e < end ? e + 1 : e;
    }
    if (rows != NULL) {
        *rows = y;
    }
    return MOY_THEME_OK;
}

int moy_theme_sheet_check(const uint8_t *src, size_t n, int rows_max, int *rows) {
    return sheet_walk(src, n, rows_max, rows, NULL, 0);
}

int moy_theme_sheet_load(const uint8_t *src, size_t n) {
    s_sheet_on = 0;
    int rc = sheet_walk(src, n, MOY_THEME_SPRITES_MAX_ROWS, NULL, NULL, 0);
    if (rc != MOY_THEME_OK) {
        return rc;
    }
    const size_t bytes = (size_t)MOY_THEME_SHEET_W * MOY_THEME_SPRITE_ROWS;
    if (s_sheet == NULL) {
        s_sheet = THEME_ALLOC(bytes);
        if (s_sheet == NULL) {
            return MOY_THEME_NOMEM;
        }
    }
    memset(s_sheet, 0, bytes);
    sheet_walk(src, n, MOY_THEME_SPRITES_MAX_ROWS, NULL, s_sheet, MOY_THEME_SPRITE_ROWS);
    for (int i = 0; i < MOY_THEME_CELLS; i++) {
        const moy_theme_cell_t *c = &moy_theme_cells[i];
        uint8_t set = 0;
        for (int y = c->y; y < c->y + c->h && !set; y++) {
            const uint8_t *row = s_sheet + (size_t)y * MOY_THEME_SHEET_W + c->x;
            for (int x = 0; x < c->w; x++) {
                set |= row[x];
            }
        }
        s_cell_set[i] = set != 0;
    }
    s_sheet_on = 1;
    return MOY_THEME_OK;
}

void moy_theme_sheet_clear(void) {
    s_sheet_on = 0;
}

const uint8_t *moy_theme_sheet(void) {
    return s_sheet_on ? s_sheet : NULL;
}

int moy_theme_sheet_cell(int part, int state, int sub) {
    if (!s_sheet_on || state < 0 || state >= MOY_THEME_STATES) {
        return -1;
    }
    for (;;) {
        for (int i = 0; i < MOY_THEME_CELLS; i++) {
            const moy_theme_cell_t *c = &moy_theme_cells[i];
            if (c->part == part && c->state == state && c->sub == sub && s_cell_set[i]) {
                return i;
            }
        }
        if (moy_theme_state_next[state] == 0xff) {
            return -1;
        }
        state = moy_theme_state_next[state];
    }
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

int moy_theme_probe(const uint8_t *src, size_t n, moy_theme_pkg_t *buf, uint8_t *notes) {
    s_probe = NULL;
    int rc = moy_theme_parse(src, n, buf);
    if (notes != NULL) {
        *notes = buf->notes;
    }
    if (rc != MOY_THEME_OK) {
        return rc;
    }
    if (buf->inherits[0]) {
        return MOY_THEME_CHAIN;
    }
    if (seed_of(buf->name, strlen(buf->name)) >= 0) {
        return MOY_THEME_TAKEN;
    }
    s_probe = buf;
    return MOY_THEME_OK;
}

void moy_theme_probe_end(void) {
    s_probe = NULL;
}

const char *moy_theme_probe_name(void) {
    return s_probe != NULL ? s_probe->name : NULL;
}

const char *moy_theme_probe_font(int role) {
    if (s_probe == NULL || role < 0 || role >= MOY_THEME_FONT_ROLES || !s_probe->font[role][0]) {
        return NULL;
    }
    return s_probe->font[role];
}

const char *moy_theme_probe_sheet(int key) {
    if (s_probe == NULL || key < 0 || key >= MOY_THEME_SHEETS || !s_probe->sheet[key][0]) {
        return NULL;
    }
    return s_probe->sheet[key];
}

// -- accent and tint: every colour role derived (docs/theming_2026-09.md §4.10) ----

// The loader's predicate: (a, b) reaches MOY_THEME_CONTRAST.
static int clears(int32_t a, int32_t b) {
    int32_t la = moy_theme_lum[a & 63], lb = moy_theme_lum[b & 63];
    int32_t hi = la > lb ? la : lb, lo = la > lb ? lb : la;
    return (hi + 500) * 10 >= MOY_THEME_CONTRAST * (lo + 500);
}

// A contrast ratio as a fraction, compared exactly.
typedef struct {
    int32_t num, den;
} ratio_t;

static ratio_t ratio(int32_t a, int32_t b) {
    int32_t la = moy_theme_lum[a & 63], lb = moy_theme_lum[b & 63];
    ratio_t r = {(la > lb ? la : lb) + 500, (la > lb ? lb : la) + 500};
    return r;
}

static int ratio_cmp(ratio_t x, ratio_t y) {
    int64_t l = (int64_t)x.num * y.den, r = (int64_t)y.num * x.den;
    return l < r ? -1 : l > r;
}

// The least ratio of colour c against each role of `bgs` (0xff past the end) in row.
static ratio_t least(int32_t c, const uint8_t *bgs, int nb, const int32_t *row) {
    ratio_t m = {0, 1};
    for (int i = 0; i < nb && bgs[i] != 0xff; i++) {
        ratio_t r = ratio(c, row[bgs[i]]);
        if (i == 0 || ratio_cmp(r, m) < 0) {
            m = r;
        }
    }
    return m;
}

static int clears_all(int32_t c, const uint8_t *bgs, int nb, const int32_t *row) {
    for (int i = 0; i < nb && bgs[i] != 0xff; i++) {
        if (!clears(c, row[bgs[i]])) {
            return 0;
        }
    }
    return 1;
}

// Step 1's map: the ramp entry nearest v in luminance among those that clear
// black and white wherever v does (the lower index on a tie); none: v.
static int32_t tint_map(int32_t v, const uint8_t *ramp, int n) {
    int need0 = clears(v, 0), need7 = clears(v, 7);
    int32_t best = -1, bestd = 0;
    for (int i = 0; i < n; i++) {
        int32_t c = ramp[i];
        if ((need0 && !clears(c, 0)) || (need7 && !clears(c, 7))) {
            continue;
        }
        int32_t d = moy_theme_lum[c] - moy_theme_lum[v];
        d = d < 0 ? -d : d;
        if (best < 0 || d < bestd || (d == bestd && c < best)) {
            best = c;
            bestd = d;
        }
    }
    return best < 0 ? v : best;
}

// A content role's value in the derive set of one side (0 dark, 1 light):
// that side's palette value, else `both`'s; -1 where neither gives one.
static int32_t set_value(int role, int side) {
    const int at[2] = {side, 2};
    for (int k = 0; k < 2; k++) {
        const uint8_t *span = moy_theme_rules_at[at[k]];
        for (int i = span[0]; i < span[0] + span[1]; i++) {
            const moy_theme_rule_t *u = &moy_theme_rules[i];
            if (u->role == role && !u->is_role) {
                return u->v;
            }
        }
    }
    return -1;
}

#define CLASS(k, i) moy_theme_classes[k][i]
#define EACH(k, i) for (int i = 0; i < MOY_THEME_CLASS_MAX && CLASS(k, i) != 0xff; i++)

static void derive_variant(const int32_t *b, int accent, int tint, int32_t *r) {
    memcpy(r, b, MOY_THEME_ROLES * sizeof(int32_t));
    const uint8_t *ramp = tint >= 0 ? &moy_theme_tints[tint][1] : NULL;
    int rn = tint >= 0 ? moy_theme_tints[tint][0] : 0;
    // 1. the tint
    if (ramp != NULL) {
        EACH(MOY_THEME_CL_BACKGROUND, i) {
            r[CLASS(MOY_THEME_CL_BACKGROUND, i)] = tint_map(b[CLASS(MOY_THEME_CL_BACKGROUND, i)],
                                                            ramp, rn);
        }
        EACH(MOY_THEME_CL_LINE, i) {
            int role = CLASS(MOY_THEME_CL_LINE, i);
            if (b[role] != 0 && b[role] != 7) {
                r[role] = tint_map(b[role], ramp, rn);
            }
        }
    }
    // 2. the accent
    if (accent >= 0) {
        int32_t a = moy_theme_accents[accent][0], deep = moy_theme_accents[accent][1];
        EACH(MOY_THEME_CL_TIED, i) {
            int role = CLASS(MOY_THEME_CL_TIED, i);
            if (b[role] == b[MOY_UI_T_TITLE] || b[role] == b[MOY_UI_T_ACCENT]) {
                r[role] = a;
            }
        }
        EACH(MOY_THEME_CL_ACCENT, i) {
            r[CLASS(MOY_THEME_CL_ACCENT, i)] = a;
        }
        EACH(MOY_THEME_CL_ACCENT_INK, i) {
            const int32_t cand[3] = {a, deep, r[MOY_UI_T_INK]};
            for (int k = 0; k < 3; k++) {
                if (clears(cand[k], r[MOY_UI_T_SURFACE])) {
                    r[CLASS(MOY_THEME_CL_ACCENT_INK, i)] = cand[k];
                    break;
                }
            }
        }
    }
    // 3. the inks of theme.pairs but the dim inks (step 4's): B's where it
    // clears every background it pairs with, else black or white, the larger
    // least ratio (black on a tie)
    for (int p = 0; p < MOY_THEME_PAIRS; p++) {
        int ink = moy_theme_pairs[p][1], first = 1;
        for (int d = 0; d < MOY_THEME_DIM_INKS; d++) {
            first &= moy_theme_dim_inks[d][0] != ink;
        }
        uint8_t bgs[MOY_THEME_PAIRS];
        int nb = 0;
        for (int q = 0; q < MOY_THEME_PAIRS; q++) {
            if (moy_theme_pairs[q][1] == ink) {
                first &= q >= p;
                bgs[nb++] = moy_theme_pairs[q][0];
            }
        }
        if (!first) {
            continue;
        }
        if (clears_all(b[ink], bgs, nb, r)) {
            r[ink] = b[ink];
        } else {
            r[ink] = ratio_cmp(least(7, bgs, nb, r), least(0, bgs, nb, r)) > 0 ? 7 : 0;
        }
    }
    // 4. the dim inks: B's where it clears, else the dimmest of the ramp and
    // the greys that does (the lower index on a tie), else the fallback ink
    for (int d = 0; d < MOY_THEME_DIM_INKS; d++) {
        const uint8_t *e = moy_theme_dim_inks[d];
        const uint8_t *bgs = &e[2];
        if (clears_all(b[e[0]], bgs, 3, r)) {
            continue;
        }
        int32_t best = -1;
        ratio_t bestr = {0, 1};
        for (int k = 0; k < rn + MOY_THEME_DIM_GREYS; k++) {
            int32_t c = k < rn ? ramp[k] : moy_theme_dim_greys[k - rn];
            if (!clears_all(c, bgs, 3, r)) {
                continue;
            }
            ratio_t m = least(c, bgs, 3, r);
            int o = best < 0 ? -1 : ratio_cmp(m, bestr);
            if (o < 0 || (o == 0 && c < best)) {
                best = c;
                bestr = m;
            }
        }
        r[e[0]] = best >= 0 ? best : r[e[1]];
    }
    // 5. content: B's where it clears R's surface, else the derive set's for
    // the surface's side where that clears, else R's ink
    int32_t s = r[MOY_UI_T_SURFACE];
    int side = ratio_cmp(ratio(s, 0), ratio(s, 7)) >= 0;
    EACH(MOY_THEME_CL_CONTENT, i) {
        int role = CLASS(MOY_THEME_CL_CONTENT, i);
        if (!clears(r[role], s)) {
            int32_t alt = set_value(role, side);
            r[role] = alt >= 0 && clears(alt, s) ? alt : r[MOY_UI_T_INK];
        }
    }
}

#undef EACH
#undef CLASS

void moy_theme_derive(const int32_t src[2][MOY_THEME_ROLES], int accent, int tint,
                      int32_t out[2][MOY_THEME_ROLES]) {
    accent = accent >= 0 && accent < MOY_THEME_ACCENTS ? accent : -1;
    tint = tint >= 0 && tint < MOY_THEME_TINTS ? tint : -1;
    for (int v = 0; v < 2; v++) {
        derive_variant(src[v], accent, tint, out[v]);
    }
}

// -- custom's theme.json -------------------------------------------------------------

// A bounded writer: what does not fit is counted, never written.
typedef struct {
    uint8_t *p;
    size_t cap, n;
} wr_t;

static void put(wr_t *w, const char *s, size_t n) {
    if (w->n + n <= w->cap) {
        memcpy(w->p + w->n, s, n);
    }
    w->n += n;
}

static void put_s(wr_t *w, const char *s) {
    put(w, s, strlen(s));
}

static void put_int(wr_t *w, int32_t v) {
    char buf[12];
    int k = sizeof(buf);
    int neg = v < 0;
    uint32_t u = neg ? (uint32_t)(-(int64_t)v) : (uint32_t)v;
    do {
        buf[--k] = (char)('0' + u % 10);
        u /= 10;
    } while (u);
    if (neg) {
        buf[--k] = '-';
    }
    put(w, buf + k, sizeof(buf) - (size_t)k);
}

static void put_str(wr_t *w, const char *s) {
    put(w, "\"", 1);
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') {
            put(w, "\\", 1);
        }
        put(w, s, 1);
    }
    put(w, "\"", 1);
}

static void put_key(wr_t *w, const char *k) {
    put_str(w, k);
    put(w, ":", 1);
}

// The source's chain flattened: each field from the nearest package that sets
// it, the default seed's own only where it is the source.
typedef struct {
    moy_theme_pkg_t *flat;
    int depth, over, layout_found;
} flat_ctx_t;

static void apply_flat(const moy_theme_pkg_t *pkg, void *ctx) {
    flat_ctx_t *c = ctx;
    moy_theme_pkg_t *f = c->flat;
    if (c->depth++ > 0 && strcmp(pkg->name, moy_theme_seed_names[0]) == 0) {
        return;
    }
    for (int i = 0; i < MOY_THEME_METRICS; i++) {
        if (f->metric[i] == MOY_THEME_NO_METRIC) {
            f->metric[i] = pkg->metric[i];
        }
    }
    for (int i = 0; i < pkg->n_effects; i++) {
        int k = 0;
        while (k < f->n_effects && memcmp(f->effect[k], pkg->effect[i], 2) != 0) {
            k++;
        }
        if (k < f->n_effects) {
            continue;
        }
        if (f->n_effects == MOY_THEME_EFFECTS_CAP) {
            c->over = 1;
            continue;
        }
        memcpy(f->effect[f->n_effects++], pkg->effect[i], 4);
    }
    for (int i = 0; i < pkg->n_surfaces; i++) {
        int k = 0;
        while (k < f->n_surfaces && memcmp(f->surface[k], pkg->surface[i], 2) != 0) {
            k++;
        }
        if (k < f->n_surfaces) {
            continue;
        }
        if (f->n_surfaces == MOY_THEME_SURFACES_CAP) {
            c->over = 1;
            continue;
        }
        memcpy(f->surface[f->n_surfaces], pkg->surface[i], 2);
        f->surface_rec[f->n_surfaces++] = pkg->surface_rec[i];
    }
    for (int i = 0; i < 2; i++) {
        if (f->frame[i] == 0) {
            f->frame[i] = pkg->frame[i];
        }
    }
    if (f->frame[2] == 0) {
        memcpy(&f->frame[2], &pkg->frame[2], 4);
    }
    for (int i = 0; i < MOY_THEME_FONT_ROLES; i++) {
        if (f->font[i][0] == 0) {
            memcpy(f->font[i], pkg->font[i], sizeof(pkg->font[i]));
        }
    }
    for (int i = 0; i < MOY_THEME_SHEETS; i++) {
        if (f->sheet[i][0] == 0) {
            memcpy(f->sheet[i], pkg->sheet[i], sizeof(pkg->sheet[i]));
        }
    }
    // A refused layout resolves as the default's, so it flattens to none.
    if (!c->layout_found && pkg->layout.set != MOY_THEME_LAYOUT_SILENT) {
        c->layout_found = 1;
        if (pkg->layout.set == MOY_THEME_LAYOUT_SET) {
            memcpy(&f->layout, &pkg->layout, sizeof(pkg->layout));
        }
    }
}

static void put_term(char *buf, size_t *k, int8_t t) {
    if (t >= 0) {
        if (t >= 10) {
            buf[(*k)++] = (char)('0' + t / 10);
        }
        buf[(*k)++] = (char)('0' + t % 10);
    } else {
        const char *s = moy_ui_token_names[-1 - t];
        size_t n = strlen(s);
        memcpy(buf + *k, s, n);
        *k += n;
    }
}

// A surface record as the grammar's text, its clauses split by `sep`.
static size_t surface_text(const moy_ui_surface_t *s, const char *sep, char *buf) {
    size_t k = 0, ns = strlen(sep);
    const char *w;
#define CLAUSE(word) do { if (k) { memcpy(buf + k, sep, ns); k += ns; } \
                          w = (word); memcpy(buf + k, w, strlen(w)); k += strlen(w); } while (0)
    if (s->edge != MOY_UI_EDGE_FLAT) {
        CLAUSE(moy_theme_edge_names[s->edge]);
    }
    if (s->fill != MOY_UI_FILL_STYLE) {
        CLAUSE(moy_theme_fill_names[s->fill - 1]);
        buf[k++] = ' ';
        put_term(buf, &k, s->a);
        if (s->fill != MOY_UI_FILL_SOLID) {
            buf[k++] = '/';
            put_term(buf, &k, s->b);
        }
    }
    if (s->flags & MOY_UI_SURF_BORDER) {
        CLAUSE("border");
        if (s->line != MOY_UI_TERM_STYLE) {
            buf[k++] = ' ';
            put_term(buf, &k, s->line);
        }
    }
    if (s->flags & MOY_UI_SURF_SHADOW) {
        CLAUSE("shadow");
    }
    if (k == 0) {
        CLAUSE(moy_theme_edge_names[MOY_UI_EDGE_FLAT]);
    }
#undef CLAUSE
    buf[k] = 0;
    return k;
}

// `text` (effects) or `surfaces`, by part then state.
static void put_parts(wr_t *w, const moy_theme_pkg_t *f, int surfaces) {
    int n = surfaces ? f->n_surfaces : f->n_effects;
    if (n == 0) {
        return;
    }
    put_s(w, surfaces ? ",\"surfaces\":{" : ",\"text\":{");
    int first_part = 1;
    for (int part = 0; part < MOY_THEME_PARTS; part++) {
        int first = 1;
        for (int st = 0; st < MOY_THEME_STATES; st++) {
            for (int i = 0; i < n; i++) {
                const uint8_t *at = surfaces ? f->surface[i] : f->effect[i];
                if (at[0] != part || at[1] != st) {
                    continue;
                }
                if (first) {
                    put_s(w, first_part ? "" : ",");
                    put_key(w, moy_theme_part_names[part]);
                    put(w, "{", 1);
                    first_part = 0;
                } else {
                    put(w, ",", 1);
                }
                first = 0;
                put_key(w, moy_theme_state_names[st]);
                if (surfaces) {
                    char buf[2 * MOY_THEME_SURFACE_LEN + 1];
                    if (surface_text(&f->surface_rec[i], ", ", buf) > MOY_THEME_SURFACE_LEN) {
                        surface_text(&f->surface_rec[i], ",", buf);
                    }
                    put_str(w, buf);
                } else {
                    put_s(w, "{\"effect\":");
                    put_str(w, moy_theme_effect_names[at[2]]);
                    if (at[2] != 0) {
                        put_s(w, ",\"color\":");
                        put_str(w, moy_theme_role_names[at[3]]);
                    }
                    put(w, "}", 1);
                }
            }
        }
        if (!first) {
            put(w, "}", 1);
        }
    }
    put(w, "}", 1);
}

static void put_layout(wr_t *w, const moy_theme_layout_t *lay) {
    if (lay->set != MOY_THEME_LAYOUT_SET) {
        return;
    }
    put_s(w, ",\"layout\":{\"bars\":[");
    for (int b = 0; b < lay->n_bars; b++) {
        const moy_theme_bar_t *bar = &lay->bar[b];
        put_s(w, b ? ",{\"edge\":" : "{\"edge\":");
        put_str(w, moy_theme_bar_edges[bar->edge]);
        if (bar->size != MOY_THEME_BAR_SIZE) {
            put_s(w, ",\"size\":");
            put_int(w, bar->size);
        }
        for (int s = 0; s < MOY_THEME_SLOTS; s++) {
            if (bar->n[s] == 0) {
                continue;
            }
            put(w, ",", 1);
            put_key(w, moy_theme_slot_names[s]);
            for (int k = 0; k < bar->n[s]; k++) {
                put(w, k ? "," : "[", 1);
                put_str(w, moy_theme_widget_names[bar->w[s][k]]);
            }
            put(w, "]", 1);
        }
        for (int t = 0; t < bar->n_text; t++) {
            put_s(w, t ? "," : ",\"text\":[");
            put_str(w, lay->text[bar->text0 + t]);
        }
        put_s(w, bar->n_text ? "]}" : "}");
    }
    put(w, "]", 1);
    if (lay->home != 0) {
        put_s(w, ",\"home\":");
        put_str(w, moy_theme_home_names[lay->home]);
    }
    put(w, "}", 1);
}

// The source's own wallpaper keys (§4.9), each value minified.
static void put_wallpaper_keys(wr_t *w, const uint8_t *src, size_t n) {
    static const char *const keys[] = {"wallpapers", "images"};
    rd_t r = {src, src + n, 1};
    if (!eat(&r, '{') || eat(&r, '}')) {
        return;
    }
    for (;;) {
        char key[16];
        int over;
        int32_t iv;
        if (str(&r, key, sizeof(key), &over) < 0 || !eat(&r, ':')) {
            return;
        }
        peek(&r);
        const uint8_t *v0 = r.p;
        if (value(&r, &iv) < 0) {
            return;
        }
        for (size_t k = 0; !over && k < sizeof(keys) / sizeof(keys[0]); k++) {
            if (strcmp(key, keys[k]) != 0) {
                continue;
            }
            put(w, ",", 1);
            put_key(w, keys[k]);
            int in = 0, esc = 0;
            for (const uint8_t *p = v0; p < r.p; p++) {
                char ch = (char)*p;
                if (!in && (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r')) {
                    continue;
                }
                put(w, &ch, 1);
                if (esc) {
                    esc = 0;
                } else if (in && ch == '\\') {
                    esc = 1;
                } else if (ch == '"') {
                    in = !in;
                }
            }
        }
        if (!eat(&r, ',')) {
            return;
        }
    }
}

static void put_custom(wr_t *w, const moy_theme_pkg_t *f, const int32_t rows[2][MOY_THEME_ROLES],
                       const uint8_t omit[2][MOY_THEME_ROLES], const uint8_t *src, size_t n) {
    put_s(w, "{\"format\":");
    put_int(w, MOY_THEME_FORMAT);
    put_s(w, ",\"name\":");
    put_str(w, MOY_THEME_CUSTOM);
    int colors = 0;
    for (int v = 0; v < 2; v++) {
        int first = 1;
        for (int i = 0; i < MOY_THEME_ROLES; i++) {
            if (omit[v][i]) {
                continue;
            }
            if (first) {
                put_s(w, colors ? ",\"" : ",\"colors\":{\"");
                put_s(w, v ? "light\":{" : "dark\":{");
                colors = 1;
            } else {
                put(w, ",", 1);
            }
            first = 0;
            put_key(w, moy_theme_role_names[i]);
            if (moy_theme_flag[i]) {
                put_s(w, rows[v][i] ? "true" : "false");
            } else {
                put_int(w, rows[v][i]);
            }
        }
        put_s(w, first ? "" : "}");
    }
    put_s(w, colors ? "}" : "");
    int any = 0;
    for (int i = 0; i < MOY_THEME_METRICS; i++) {
        if (f->metric[i] == MOY_THEME_NO_METRIC) {
            continue;
        }
        put_s(w, any ? "," : ",\"metrics\":{");
        any = 1;
        put_key(w, moy_theme_metric_names[i]);
        put_int(w, f->metric[i]);
    }
    put_s(w, any ? "}" : "");
    if (f->frame[0] || f->frame[1] || f->frame[2]) {
        const char *sep = "";
        put_s(w, ",\"frame\":{");
        if (f->frame[0]) {
            put_key(w, "style");
            put_str(w, moy_theme_frame_names[f->frame[0] - 1]);
            sep = ",";
        }
        if (f->frame[1]) {
            put_s(w, sep);
            put_key(w, "title_align");
            put_str(w, moy_theme_align_names[f->frame[1] - 1]);
            sep = ",";
        }
        if (f->frame[2]) {
            char order[5] = {0};
            memcpy(order, &f->frame[2], 4);
            put_s(w, sep);
            put_key(w, "buttons");
            put_str(w, order);
        }
        put(w, "}", 1);
    }
    any = 0;
    for (int i = 0; i < MOY_THEME_FONT_ROLES; i++) {
        if (f->font[i][0] == 0) {
            continue;
        }
        put_s(w, any ? "," : ",\"fonts\":{");
        any = 1;
        put_key(w, moy_theme_font_roles[i]);
        put_str(w, f->font[i]);
    }
    put_s(w, any ? "}" : "");
    for (int i = 0; i < MOY_THEME_SHEETS; i++) {
        if (f->sheet[i][0]) {
            put(w, ",", 1);
            put_key(w, moy_theme_sheet_keys[i]);
            put_str(w, f->sheet[i]);
        }
    }
    put_parts(w, f, 0);
    put_parts(w, f, 1);
    put_layout(w, &f->layout);
    put_wallpaper_keys(w, src, n);
    put(w, "}", 1);
}

int moy_theme_custom(const uint8_t *src, size_t n, int accent, int tint,
                     moy_theme_pkg_t scratch[2], uint8_t *out, size_t cap, size_t *len) {
    moy_theme_pkg_t *pkg = &scratch[0], *flat = &scratch[1];
    const moy_theme_pkg_t *was = s_probe;
    *len = 0;
    int rc = moy_theme_parse(src, n, pkg);
    if (rc != MOY_THEME_OK) {
        return rc;
    }
    char name[MOY_THEME_NAME_MAX + 1];
    strcpy(name, pkg->name);
    size_t nn = strlen(name);
    int seed = seed_of(name, nn);
    if (seed >= 0) {
        if (n != moy_theme_seed_len[seed] || memcmp(src, moy_theme_seed_src[seed], n) != 0) {
            return MOY_THEME_TAKEN;
        }
    } else if (pkg->inherits[0]) {
        return MOY_THEME_CHAIN;
    } else {
        s_probe = pkg;
    }
    int32_t base[2][MOY_THEME_ROLES], rows[2][MOY_THEME_ROLES];
    static const uint8_t keep[2][MOY_THEME_ROLES];
    flat_ctx_t c = {flat, 0, 0, 0};
    memset(flat, 0, sizeof(*flat));
    memset(flat->metric, MOY_THEME_NO_METRIC, sizeof(flat->metric));
    rc = resolve_row(name, nn, MOY_THEME_DARK, base[0]);
    if (rc == MOY_THEME_OK) {
        rc = resolve_row(name, nn, MOY_THEME_LIGHT, base[1]);
    }
    if (rc == MOY_THEME_OK) {
        rc = chain(name, nn, apply_flat, &c);
    }
    s_probe = was;
    if (rc != MOY_THEME_OK) {
        return rc;
    }
    if (c.over) {
        return MOY_THEME_BIG;
    }
    moy_theme_derive((const int32_t(*)[MOY_THEME_ROLES])base, accent, tint, rows);
    wr_t w = {out, cap, 0};
    put_custom(&w, flat, (const int32_t(*)[MOY_THEME_ROLES])rows, keep, src, n);
    if (w.n > MOY_THEME_BYTES) {
        // Over the cap: leave out each role whose value §5's resolution
        // gives back, checked by resolving what is left.
        uint8_t omit[2][MOY_THEME_ROLES];
        moy_theme_pkg_t *probe = &scratch[0];
        memset(omit, 0, sizeof(omit));
        memset(probe, 0, sizeof(*probe));
        strcpy(probe->name, MOY_THEME_CUSTOM);
        memset(probe->metric, MOY_THEME_NO_METRIC, sizeof(probe->metric));
        for (int v = 0; v < 2; v++) {
            for (int i = 0; i < MOY_THEME_ROLES; i++) {
                probe->role[v][i] = (int8_t)rows[v][i];
            }
        }
        s_probe = probe;
        for (int v = 0; v < 2; v++) {
            for (int i = 0; i < MOY_THEME_ROLES; i++) {
                int32_t got[MOY_THEME_ROLES];
                probe->role[v][i] = MOY_THEME_UNSET;
                if (resolve_row(MOY_THEME_CUSTOM, strlen(MOY_THEME_CUSTOM), v, got) == MOY_THEME_OK
                    && memcmp(got, rows[v], sizeof(got)) == 0) {
                    omit[v][i] = 1;
                } else {
                    probe->role[v][i] = (int8_t)rows[v][i];
                }
            }
        }
        s_probe = was;
        w.n = 0;
        put_custom(&w, flat, (const int32_t(*)[MOY_THEME_ROLES])rows,
                   (const uint8_t(*)[MOY_THEME_ROLES])omit, src, n);
    }
    *len = w.n;
    return w.n > MOY_THEME_BYTES || w.n > cap ? MOY_THEME_BIG : MOY_THEME_OK;
}

const int moy_theme_custom_dims[4] = {
    MOY_THEME_ACCENTS, MOY_THEME_TINTS, MOY_THEME_RAMP_MAX, MOY_THEME_BYTES,
};

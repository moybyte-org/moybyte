// Templates and conditions (the spec's §3): compiled from their source at the
// call that names them, against the declared slots and the list's model;
// evaluated by the walk. The reference's compiler (runtime/uitree/uitree.py's
// `tmpl`, `cond`) is the specification; a form it takes and this one does not
// yet is refused by name, never drawn wrong.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "moy_uitree_int.h"

const char *uit_type_name(int type)
{
    switch (type) {
    case UIT_BOOL: return "bool";
    case UIT_INT: return "int";
    case UIT_NUM: return "number";
    case UIT_TEXT: return "text";
    case UIT_ENUM: return "enum";
    case UIT_ROWOF: return "row_of";
    case UIT_MODEL: return "model";
    }
    return "?";
}

int uit_slot_find(const uit_t *e, const char *name, size_t n)
{
    for (int i = 0; i < e->nslots; i++) {
        const char *s = uit_str(e, e->slots[i].name);
        if (strlen(s) == n && !memcmp(s, name, n))
            return i;
    }
    return -1;
}

int uit_field_find(const uit_t *e, int model, const char *name, size_t n)
{
    if (model < 0)
        return -1;
    const uit_slot_t *M = &e->slots[model];
    for (int f = 0; f < M->nf; f++) {
        const char *s = uit_str(e, M->mf->fname[f]);
        if (strlen(s) == n && !memcmp(s, name, n))
            return f;
    }
    return -1;
}

const char *uit_cell(const uit_t *e, int model, int row, int f)
{
    const uit_slot_t *M = &e->slots[model];
    if (row < 0 || row >= M->nrows || f < 0 || f >= M->nf)
        return "";
    return M->cells + M->at[row * M->nf + f];
}

// Whether row `row`'s key is what slot `slot` holds: the reference's
// `row.key == s.current`, so an int slot matches an int key and a text or
// row_of slot a text key.
int uit_row_slot_current(const uit_t *e, int slot, int model, int row)
{
    const uit_slot_t *S = &e->slots[slot], *M = &e->slots[model];
    if (row < 0 || row >= M->nrows)
        return 0;
    const char *k = uit_cell(e, model, row, M->key);
    int kt = M->mf->ftype[M->key];
    if (S->type == UIT_INT)
        return kt == UIT_INT && strtol(k, NULL, 10) == S->i;
    return kt == UIT_TEXT && strlen(k) == S->sn && !memcmp(k, S->s ? S->s : "", S->sn);
}

int uit_row_current(const uit_t *e, int list, int model, int row)
{
    int s = list >= 0 ? e->nodes[list].current : -1;
    return s >= 0 && uit_row_slot_current(e, s, model, row);
}

int uit_row_find(const uit_t *e, int model, const char *key, size_t n)
{
    const uit_slot_t *M = &e->slots[model];
    for (int r = 0; r < M->nrows; r++) {
        const char *k = uit_cell(e, model, r, M->key);
        if (strlen(k) == n && !memcmp(k, key, n))
            return r;
    }
    return -1;
}

static void trim(const char **s, size_t *n)
{
    while (*n && (**s == ' ' || **s == '\t'))
        (*s)++, (*n)--;
    while (*n && ((*s)[*n - 1] == ' ' || (*s)[*n - 1] == '\t'))
        (*n)--;
}

// -- a reference: its kind and its type -----------------------------------------

typedef struct {
    uint8_t rk, type;
    int16_t ref, f;
} ref_t;

static const char *const SYS_NAMES[] = {"", "desk", "handheld", "wide", "tall", "touch"};
// every system value the spec names, and the retired ones (N04)
static const char *const SYS_ALL[] = {"desk", "handheld", "wide", "tall", "current", "focus", "touch", "input"};
static const char *const SYS_RETIRED[] = {"symbols", "w", "h", "fs"};
static const char *const INPUT_NAMES[] = {"keys", "tdeck", "pad", "touch"};

static int is_word(const char *s, size_t n, const char *w)
{
    return strlen(w) == n && !memcmp(s, w, n);
}

static int ref_parse(uit_t *e, const char *s, size_t n, int model, ref_t *r)
{
    trim(&s, &n);
    if (!n)
        return uit_check_fail(e, "T02", UIT_MSG("an empty name", ""));
    if (s[0] == '@') {
        for (int i = 1; i <= SYS_TOUCH; i++)
            if (is_word(s + 1, n - 1, SYS_NAMES[i])) {
                *r = (ref_t){RK_SYS, UIT_BOOL, (int16_t)i, -1};
                return 0;
            }
        if (is_word(s, n, "@focus")) {
            *r = (ref_t){RK_FOCUS, UIT_TEXT, -1, -1};
            return 0;
        }
        if (is_word(s, n, "@input")) {
            *r = (ref_t){RK_INPUT, UIT_ENUM, -1, -1};
            return 0;
        }
        for (size_t i = 0; i < sizeof SYS_RETIRED / sizeof *SYS_RETIRED; i++)
            if (is_word(s + 1, n - 1, SYS_RETIRED[i]))
                return uit_check_fail(e, "N04", UIT_MSG("%.*s is retired", "%.*s"), (int)n, s);
        if (is_word(s, n, "@current")) {
            if (model < 0 || e->ccur < 0)
                return uit_check_fail(e, "T05", UIT_MSG("@current outside a list with current=", ""));
            *r = (ref_t){RK_CURRENT, UIT_BOOL, (int16_t)e->ccur, -1};
            return 0;
        }
        for (size_t i = 0; i < sizeof SYS_ALL / sizeof *SYS_ALL; i++)
            if (is_word(s + 1, n - 1, SYS_ALL[i]))
                return uit_fail(e, UIT_E_LANG, UIT_MSG("system value %.*s", "%.*s"), (int)n, s);
        return uit_check_fail(e, "T02", UIT_MSG("no system value %.*s", "%.*s"), (int)n, s);
    }
    if (s[0] == '.') {
        if (model < 0)
            return uit_check_fail(e, "T05", UIT_MSG("{%.*s}: a row's field outside a list", "{%.*s}"), (int)n, s);
        int f = uit_field_find(e, model, s + 1, n - 1);
        if (f < 0)
            return uit_check_fail(e, "T02", UIT_MSG("rows have no field %.*s", "%.*s"), (int)(n - 1), s + 1);
        *r = (ref_t){RK_FIELD, e->slots[model].mf->ftype[f], -1, (int16_t)f};
        return 0;
    }
    const char *dot = memchr(s, '.', n);
    size_t bn = dot ? (size_t)(dot - s) : n;
    int slot = uit_slot_find(e, s, bn);
    if (slot < 0)
        return uit_check_fail(e, "T02", UIT_MSG("{%.*s}: no state %.*s", "{%.*s} %.*s"), (int)n, s, (int)bn, s);
    const uit_slot_t *S = &e->slots[slot];
    if (!dot) {
        *r = (ref_t){RK_SLOT, S->type, (int16_t)slot, -1};
        return 0;
    }
    if (S->type != UIT_ROWOF || S->model < 0)
        return uit_check_fail(e, "T03", UIT_MSG("{%.*s}: %.*s is a %s; it has no fields", "{%.*s} %.*s %s"), (int)n, s, (int)bn, s,
                              uit_type_name(S->type));
    int f = uit_field_find(e, S->model, dot + 1, n - bn - 1);
    if (f < 0)
        return uit_check_fail(e, "T02", UIT_MSG("rows of %s have no field %.*s", "%s %.*s"), uit_str(e, e->slots[S->model].name),
                              (int)(n - bn - 1), dot + 1);
    *r = (ref_t){RK_ROWOF, e->slots[S->model].mf->ftype[f], (int16_t)slot, (int16_t)f};
    return 0;
}

// An enum reference's values: the slot's, or @input's.
static int enum_n(const uit_t *e, const ref_t *r)
{
    return r->rk == RK_INPUT ? 4 : e->slots[r->ref].nvals;
}

static const char *enum_at(const uit_t *e, const ref_t *r, int i)
{
    if (r->rk == RK_INPUT)
        return INPUT_NAMES[i];
    const char *v = uit_str(e, e->slots[r->ref].vals);
    while (i--)
        v += strlen(v) + 1;
    return v;
}

static int enum_has(const uit_t *e, const ref_t *r, const char *s, size_t n)
{
    for (int i = 0; i < enum_n(e, r); i++)
        if (is_word(s, n, enum_at(e, r, i)))
            return 1;
    return 0;
}

// A value now: its text (into *s, *n) and its number (into *i, *d).
typedef struct {
    uint8_t type;
    int32_t i;
    double d;
    const char *s;
    size_t n;
} val_t;

static void val_of_text(val_t *v, int type, const char *t)
{
    v->type = (uint8_t)type;
    v->s = t;
    v->n = strlen(t);
    v->i = (type == UIT_INT || type == UIT_BOOL) ? (int32_t)strtol(t, NULL, 10) : 0;
    v->d = type == UIT_NUM ? strtod(t, NULL) : v->i;
}

static void ref_get(uit_t *e, uint8_t rk, int16_t ref, int16_t f, int model, int row, val_t *v)
{
    memset(v, 0, sizeof *v);
    v->s = "";
    switch (rk) {
    case RK_SLOT: {
        const uit_slot_t *S = &e->slots[ref];
        if (S->absent) {
            v->type = UIT_TEXT;         // absent: drawn as nothing, false in a condition
            break;
        }
        v->type = S->type;
        v->i = S->i;
        v->d = S->f;
        if (S->type == UIT_MODEL)
            v->i = S->nrows;
        v->s = S->s ? S->s : "";
        v->n = S->sn;
        break;
    }
    case RK_FIELD:
        if (model >= 0)
            val_of_text(v, e->slots[model].mf->ftype[f], uit_cell(e, model, row, f));
        break;
    case RK_ROWOF: {
        const uit_slot_t *S = &e->slots[ref];
        int m = S->model;
        int r = uit_row_find(e, m, S->s ? S->s : "", S->sn);
        val_of_text(v, e->slots[m].mf->ftype[f], r < 0 ? "" : uit_cell(e, m, r, f));
        break;
    }
    case RK_SYS: {
        int desk = (e->flags & UIT_F_DESK) != 0, wide = e->cw * 3 > e->ch * 4;
        int on = ref == SYS_DESK ? desk : ref == SYS_HANDHELD ? !desk : ref == SYS_WIDE ? wide
                 : ref == SYS_TALL ? !wide : e->input == UIT_IN_TOUCH;
        v->type = UIT_BOOL;
        v->i = on;
        break;
    }
    case RK_CURRENT:
        v->type = UIT_BOOL;
        v->i = model >= 0 && uit_row_slot_current(e, ref, model, row);
        break;
    case RK_FOCUS:
        v->type = UIT_TEXT;
        v->s = e->focus_key;
        v->n = strlen(e->focus_key);
        break;
    case RK_INPUT:
        v->type = UIT_ENUM;
        v->s = INPUT_NAMES[e->input & 3];
        v->n = strlen(v->s);
        break;
    }
}

static void val_text(uit_buf_t *b, const val_t *v)
{
    switch (v->type) {
    case UIT_BOOL:
        uit_buf_printf(b, "%s", v->i ? "True" : "False");
        break;
    case UIT_INT:
        uit_buf_printf(b, "%ld", (long)v->i);
        break;
    case UIT_NUM: {
        // Python's str(float): the shortest text that reads back, ".0" when whole
        char t[32];
        for (int p = 1; p <= 17; p++) {
            snprintf(t, sizeof t, "%.*g", p, v->d);
            if (strtod(t, NULL) == v->d)
                break;
        }
        uit_buf_printf(b, "%s%s", t, strpbrk(t, ".en") ? "" : ".0");
        break;
    }
    default:
        if (v->n) {
            uint32_t at = b->n;
            uit_buf_printf(b, "%.*s", (int)v->n, v->s);
            (void)at;
        }
    }
}

// -- conditions -----------------------------------------------------------------

static int term_parse(uit_t *e, const char *s, size_t n, int model, uit_term_t *t)
{
    trim(&s, &n);
    memset(t, 0, sizeof *t);
    t->ls = UIT_NONE;
    if (n && s[0] == '!' && !(n > 1 && s[1] == '=')) {
        t->neg = 1;
        s++, n--;
    }
    const char *op = NULL;
    size_t opn = 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] == '!' && i + 1 < n && s[i + 1] == '=') {
            op = s + i, opn = 2, t->cmp = 2;
            break;
        }
        if (s[i] == '=') {
            op = s + i, opn = 1, t->cmp = 1;
            break;
        }
    }
    ref_t r;
    int got = ref_parse(e, s, op ? (size_t)(op - s) : n, model, &r);
    if (got < 0)
        return got;
    t->rk = r.rk;
    t->ref = r.ref;
    t->f = r.f;
    if (!op) {
        if (r.type == UIT_ENUM)
            return uit_check_fail(e, "T03", UIT_MSG("%.*s is an enum: it has no truth", "%.*s is an enum"), (int)n, s);
        return 0;
    }
    const char *lit = op + opn;
    size_t ln = n - (size_t)(lit - s);
    trim(&lit, &ln);
    if (r.type == UIT_BOOL)
        return uit_check_fail(e, "T03", UIT_MSG("%.*s compares a bool", "%.*s"), (int)n, s);
    if (r.type == UIT_MODEL)
        return uit_check_fail(e, "T03", UIT_MSG("%.*s: a model is not compared", "%.*s"), (int)n, s);
    if (r.type == UIT_ENUM && !enum_has(e, &r, lit, ln))
        return uit_check_fail(e, "T03", UIT_MSG("%.*s: no such value %.*s", "%.*s %.*s"), (int)n, s, (int)ln, lit);
    char tmp[64];
    size_t m = ln < sizeof tmp - 1 ? ln : sizeof tmp - 1;
    memcpy(tmp, lit, m);
    tmp[m] = 0;
    char *stop = tmp;
    if (r.type == UIT_INT)
        t->li = (int32_t)strtol(tmp, &stop, 10);
    else
        t->li = (int32_t)strtol(tmp, NULL, 10);
    t->lf = strtod(tmp, r.type == UIT_NUM ? &stop : NULL);
    if ((r.type == UIT_INT || r.type == UIT_NUM) && (!m || m != ln || *stop))
        return uit_check_fail(e, "T03", UIT_MSG("%.*s: %.*s is not a number", "%.*s %.*s"), (int)n, s, (int)ln, lit);
    int at = uit_buf_put(&e->pool, lit, ln);
    if (at < 0 || at >= (int)UIT_NONE)
        return UIT_E_NOMEM;
    t->ls = (uint16_t)at;
    return 0;
}

int uit_cond_compile(uit_t *e, const char *src, int model)
{
    size_t n = strlen(src);
    const char *t0 = src;
    size_t tn = n;
    trim(&t0, &tn);
    if (!tn)
        return uit_check_fail(e, "C01", UIT_MSG("a condition is a text, not empty", ""));
    if (strpbrk(src, "{?"))
        return uit_check_fail(e, "C01", UIT_MSG("condition %s is bare, not a template", "%s"), src);
    int any = strchr(src, '|') != NULL;
    if (any && strchr(src, '&'))
        return uit_check_fail(e, "C02", UIT_MSG("condition %s mixes & and |", "%s"), src);
    if (uit_grow((void **)&e->conds, &e->cconds, e->nconds + 1, sizeof *e->conds))
        return UIT_E_NOMEM;
    int sat = uit_buf_put(&e->pool, src, n);
    if (sat < 0 || sat >= (int)UIT_NONE)
        return UIT_E_NOMEM;
    uit_cond_t C = {(uint16_t)sat, (uint16_t)e->nterms, 0, (uint8_t)any};
    if (!strcmp(src, "True") || !strcmp(src, "False")) {
        // a literal (focus=True): no terms, true as an empty "&", false as an empty "|"
        C.any = src[0] == 'F';
        e->conds[e->nconds] = C;
        return e->nconds++;
    }
    char sep = any ? '|' : '&';
    const char *p = src;
    for (;;) {
        const char *q = strchr(p, sep);
        size_t len = q ? (size_t)(q - p) : strlen(p);
        if (uit_grow((void **)&e->terms, &e->cterms, e->nterms + 1, sizeof *e->terms))
            return UIT_E_NOMEM;
        uit_term_t t;
        int r = term_parse(e, p, len, model, &t);
        if (r < 0)
            return r;
        e->terms[e->nterms++] = t;
        C.n++;
        if (!q)
            break;
        p = q + 1;
    }
    e->conds[e->nconds] = C;
    return e->nconds++;
}

static int term_eval(uit_t *e, const uit_term_t *t, int model, int row)
{
    val_t v;
    ref_get(e, t->rk, t->ref, t->f, model, row, &v);
    int on;
    if (!t->cmp) {
        switch (v.type) {
        case UIT_BOOL: case UIT_INT: case UIT_MODEL: on = v.i != 0; break;
        case UIT_NUM: on = v.d != 0; break;
        default: on = v.n > 0;
        }
    } else {
        int eq;
        if (v.type == UIT_INT)
            eq = v.i == t->li;
        else if (v.type == UIT_NUM)
            eq = v.d == t->lf;
        else {
            const char *l = uit_str(e, t->ls);
            eq = strlen(l) == v.n && !memcmp(l, v.s, v.n);
        }
        on = t->cmp == 1 ? eq : !eq;
    }
    return on;
}

int uit_cond_eval(uit_t *e, int c, int model, int row)
{
    const uit_cond_t *C = &e->conds[c];
    for (int i = 0; i < C->n; i++) {
        int on = term_eval(e, &e->terms[C->first + i], model, row) != e->terms[C->first + i].neg;
        if (C->any && on)
            return 1;
        if (!C->any && !on)
            return 0;
    }
    return !C->any;
}

// -- templates ------------------------------------------------------------------

static int op_add(uit_t *e, uit_op_t o)
{
    if (uit_grow((void **)&e->ops, &e->cops, e->nops + 1, sizeof *e->ops))
        return UIT_E_NOMEM;
    e->ops[e->nops] = o;
    return e->nops++;
}

static int lit_add(uit_t *e, const char *s, size_t n)
{
    int at = uit_buf_put(&e->pool, s, n);
    if (at < 0 || at >= (int)UIT_NONE)
        return UIT_E_NOMEM;
    uit_op_t o = {OP_LIT, 0, 0, 0, (uint16_t)at, (uint16_t)n};
    return op_add(e, o);
}

// The source unescaped of `\:` and `\?` (a choice's branch).
static size_t unescape(char *dst, const char *s, size_t n)
{
    size_t m = 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] == '\\' && i + 1 < n && (s[i + 1] == ':' || s[i + 1] == '?'))
            i++;
        dst[m++] = s[i];
    }
    dst[m] = 0;
    return m;
}

static const char *find_unescaped(const char *s, size_t n, char ch)
{
    for (size_t i = 0; i < n; i++)
        if (s[i] == ch && (i == 0 || s[i - 1] != '\\'))
            return s + i;
    return NULL;
}

static int tmpl_body(uit_t *e, const char *src, int model, uit_tmpl_t *T);

// Python's `x.lstrip("-").isdigit()`: a branch that is a whole number.
static int is_int(const char *s)
{
    while (*s == '-')
        s++;
    if (!*s)
        return 0;
    for (; *s; s++)
        if (*s < '0' || *s > '9')
            return 0;
    return 1;
}

// A branch holds another choice: a '?' between one of its '{' and the '}' after.
static int holds_choice(const char *s)
{
    // the reference's `br.split("{")[1:]`: the text from each '{' to the next
    for (const char *a = strchr(s, '{'); a; a = strchr(a + 1, '{')) {
        const char *nx = strchr(a + 1, '{');
        size_t len = nx ? (size_t)(nx - a - 1) : strlen(a + 1);
        const char *z = memchr(a + 1, '}', len);
        if (z && memchr(a + 1, '?', (size_t)(z - a - 1)))
            return 1;
    }
    return 0;
}

static int label_has(const uit_t *e, const uit_labels_t *L, const char *s, size_t n)
{
    const char *k = uit_str(e, L->keys);
    for (int i = 0; i < L->n; i++, k += strlen(k) + 1)
        if (is_word(s, n, k))
            return 1;
    return 0;
}

// `{x|map NAME}`'s table against x's type: every enum value and no other, a
// bool's True and False, any other type's "_" (T04).
static int map_check(uit_t *e, const ref_t *r, int table, const char *name)
{
    const uit_labels_t *L = &e->labels[table];
    if (r->type == UIT_ENUM) {
        int n = enum_n(e, r);
        for (int i = 0; i < n; i++) {
            const char *v = enum_at(e, r, i);
            if (!label_has(e, L, v, strlen(v)))
                return uit_check_fail(e, "T04", UIT_MSG("table %s has no label for %s", "%s %s"), name, v);
        }
        const char *k = uit_str(e, L->keys);
        for (int i = 0; i < L->n; i++, k += strlen(k) + 1)
            if (!enum_has(e, r, k, strlen(k)))
                return uit_check_fail(e, "T04", UIT_MSG("table %s: %s is no value of the enum", "table %s %s"), name, k);
        return 0;
    }
    if (r->type == UIT_BOOL) {
        if (L->n != 2 || !label_has(e, L, "True", 4) || !label_has(e, L, "False", 5))
            return uit_check_fail(e, "T04", UIT_MSG("table %s: a bool's table has True and False", "table %s"), name);
        return 0;
    }
    if (!label_has(e, L, "_", 1))
        return uit_check_fail(e, "T04", UIT_MSG("table %s on a %s needs a default \"_\"", "%s %s"), name, uit_type_name(r->type));
    return 0;
}

// `fmt`'s one printf spec: the reference keys what it formats on the spec's
// last letter, and Python's `%` refuses anything but one conversion. The
// conversion this engine prints with is the one parsed (`*conv`).
static int fmt_check(uit_t *e, const char *a, size_t an, int type, char *conv)
{
    char last = an ? a[an - 1] : 0;
    int ints = type == UIT_INT, nums = ints || type == UIT_NUM;
    int ok;
    switch (last) {
    case 'd': case 'i': case 'x': ok = ints; break;
    case 'f': case 'e': case 'g': ok = nums; break;
    case 's': ok = nums || type == UIT_TEXT || type == UIT_ENUM || type == UIT_ROWOF; break;
    default:
        return uit_check_fail(e, "T01", UIT_MSG("fmt takes one printf spec: %.*s", "fmt takes one printf spec %.*s"), (int)an, a);
    }
    if (a[0] != '%')
        return uit_check_fail(e, "T01", UIT_MSG("fmt takes one printf spec: %.*s", "fmt takes one printf spec %.*s"), (int)an, a);
    if (!ok)
        return uit_check_fail(e, "T03", UIT_MSG("%.*s does not format a %s", "%.*s %s"), (int)an, a, uit_type_name(type));
    size_t i = 1;
    while (i < an && strchr("-+ #0", a[i]))
        i++;
    while (i < an && a[i] >= '0' && a[i] <= '9')
        i++;
    if (i < an && a[i] == '.')
        for (i++; i < an && a[i] >= '0' && a[i] <= '9'; i++)
            ;
    if (i < an && strchr("lhL", a[i]))
        return uit_fail(e, UIT_E_LANG, UIT_MSG("fmt %.*s: a length modifier", "fmt %.*s"), (int)an, a);
    if (i >= an || !strchr("dixfegs", a[i]))
        return uit_check_fail(e, "T01", UIT_MSG("fmt %.*s: a bad spec", "fmt %.*s"), (int)an, a);
    *conv = a[i];
    int pct = 0;
    for (size_t k = i + 1; k < an; k++)
        if (a[k] == '%') {
            if (k + 1 < an && a[k + 1] == '%')
                k++;
            else
                pct = 1;
        }
    if (pct)
        return uit_check_fail(e, "T01", UIT_MSG("fmt %.*s: one spec of one value", "fmt %.*s"), (int)an, a);
    if (*conv == 's' && type != UIT_TEXT && type != UIT_ENUM && type != UIT_ROWOF && type != UIT_INT &&
        type != UIT_NUM)
        return uit_fail(e, UIT_E_LANG, UIT_MSG("fmt %%s of a %s", "%s %s"), uit_type_name(type));
    if (strchr("dix", *conv) && type != UIT_INT)
        return uit_fail(e, UIT_E_LANG, UIT_MSG("fmt %c of a %s", "%c %s"), *conv, uit_type_name(type));
    return 0;
}

// One `{...}`: a choice, or a reference and at most one transform (after an
// optional count). `*ty` is what it renders, as the reference types it.
static int piece(uit_t *e, const char *b, size_t n, int model, uit_tmpl_t *T, uint8_t *ty)
{
    const char *q = find_unescaped(b, n, '?');
    if (q) {
        char cs[128];
        size_t cn = (size_t)(q - b);
        if (cn >= sizeof cs)
            return uit_fail(e, UIT_E_LANG, UIT_MSG("a long condition", ""));
        memcpy(cs, b, cn);
        cs[cn] = 0;
        const char *s = cs;
        trim(&s, &cn);
        char cs2[128];
        memcpy(cs2, s, cn);
        cs2[cn] = 0;
        int c = uit_cond_compile(e, cs2, model);
        if (c < 0)
            return c;
        const char *rest = q + 1;
        size_t rn = n - (size_t)(rest - b);
        const char *colon = find_unescaped(rest, rn, ':');
        if (!colon)
            return uit_check_fail(e, "T01", UIT_MSG("{cond?a:b} needs both branches", ""));
        if (find_unescaped(colon + 1, rn - (size_t)(colon + 1 - rest), ':'))
            return uit_check_fail(e, "T01", UIT_MSG("a second ':' in {cond?a:b}", ""));
        char *br = UIT_MALLOC(rn + 1);
        if (!br)
            return UIT_E_NOMEM;
        int sub[2], ints = 1, braces = 0;
        for (int k = 0; k < 2; k++) {
            const char *s0 = k ? colon + 1 : rest;
            size_t sn = k ? rn - (size_t)(colon + 1 - rest) : (size_t)(colon - rest);
            unescape(br, s0, sn);
            braces |= strchr(br, '{') != NULL;
            ints &= is_int(br);
            if (holds_choice(br)) {
                UIT_FREE(br);
                return uit_check_fail(e, "T01", UIT_MSG("a branch holds another choice", ""));
            }
        }
        for (int k = 0; k < 2; k++) {
            const char *s0 = k ? colon + 1 : rest;
            size_t sn = k ? rn - (size_t)(colon + 1 - rest) : (size_t)(colon - rest);
            unescape(br, s0, sn);
            sub[k] = uit_tmpl_compile(e, br, model);
            if (sub[k] < 0) {
                UIT_FREE(br);
                return sub[k];
            }
        }
        UIT_FREE(br);
        if (e->tmpls[sub[0]].row || e->tmpls[sub[1]].row)
            T->row = 1;
        T->lit = 0;
        *ty = ints && !braces ? UIT_INT : UIT_TEXT;
        uit_op_t o = {OP_CHOICE, 0, (int16_t)c, -1, (uint16_t)sub[0], (uint16_t)sub[1]};
        return op_add(e, o);
    }
    const char *bar = memchr(b, '|', n);
    ref_t r;
    int got = ref_parse(e, b, bar ? (size_t)(bar - b) : n, model, &r);
    if (got < 0)
        return got;
    if (r.rk == RK_FIELD)
        T->row = 1;
    T->lit = 0;
    if (!bar) {
        *ty = r.type == UIT_ENUM ? UIT_TEXT : r.type;
        uit_op_t o = {OP_VAL, r.rk, r.ref, r.f, 0, 0};
        return op_add(e, o);
    }
    // the transforms: "|"-separated, each trimmed
    const char *p = bar + 1, *end = b + n;
    const char *nx = memchr(p, '|', (size_t)(end - p));
    const char *w = p;
    size_t wn = (size_t)((nx ? nx : end) - p);
    trim(&w, &wn);
    int type = r.type, counted = 0;
    *ty = UIT_TEXT;
    if (is_word(w, wn, "count")) {
        if (r.type != UIT_MODEL)
            return uit_check_fail(e, "T03", UIT_MSG("count counts a model's rows; %.*s is a %s", "%.*s %s"), (int)(bar - b), b,
                                  uit_type_name(r.type));
        type = UIT_INT;
        counted = 1;
        if (!nx) {
            *ty = UIT_INT;
            uit_op_t o = {OP_COUNT, r.rk, r.ref, r.f, 0, 0};
            return op_add(e, o);
        }
        p = nx + 1;
        nx = memchr(p, '|', (size_t)(end - p));
        w = p;
        wn = (size_t)((nx ? nx : end) - p);
        trim(&w, &wn);
    }
    if (is_word(w, wn, "bytes")) {
        if (type != UIT_INT || nx)
            return uit_check_fail(e, "T03", UIT_MSG("bytes formats an int, last", ""));
        uit_op_t o = {OP_BYTES, r.rk, r.ref, r.f, 0, 0};
        return op_add(e, o);
    }
    if (nx)
        return uit_check_fail(e, "T01", UIT_MSG("one transform per name", ""));
    const char *sp = memchr(w, ' ', wn);
    size_t on = sp ? (size_t)(sp - w) : wn;
    const char *a = sp ? sp + 1 : w + wn;
    size_t an = (size_t)(w + wn - a);
    trim(&a, &an);
    if (is_word(w, on, "fmt")) {
        char conv = 0;
        int f = fmt_check(e, a, an, type, &conv);
        if (f < 0)
            return f;
        if (counted && conv == 's')
            return uit_fail(e, UIT_E_LANG, UIT_MSG("count then fmt %%s", "%s"));
        int spec = uit_buf_put(&e->pool, a, an);
        if (spec < 0 || spec >= (int)UIT_NONE)
            return UIT_E_NOMEM;
        uit_op_t o = {OP_FMT, r.rk, r.ref, r.f, (uint16_t)spec, (uint16_t)conv};
        return op_add(e, o);
    }
    if (is_word(w, on, "map")) {
        for (int i = 0; i < e->nlabels; i++) {
            const char *nm = uit_str(e, e->labels[i].name);
            if (is_word(a, an, nm)) {
                if (counted)
                    return uit_fail(e, UIT_E_LANG, UIT_MSG("count then map", ""));
                int m = map_check(e, &r, i, nm);
                if (m < 0)
                    return m;
                uit_op_t o = {OP_MAP, r.rk, r.ref, r.f, (uint16_t)i, 0};
                return op_add(e, o);
            }
        }
        return uit_check_fail(e, "T02", UIT_MSG("no label table %.*s", "%.*s"), (int)an, a);
    }
    if (is_word(w, on, "plural")) {
        if (type != UIT_INT)
            return uit_check_fail(e, "T03", UIT_MSG("plural of a %s", "%s"), uit_type_name(type));
        const char *comma = memchr(a, ',', an);
        if (!comma || comma == a || comma + 1 == a + an)
            return uit_check_fail(e, "T01", UIT_MSG("plural needs both forms", ""));
        int one = uit_buf_put(&e->pool, a, (size_t)(comma - a));
        int many = uit_buf_put(&e->pool, comma + 1, an - (size_t)(comma + 1 - a));
        if (one < 0 || many < 0 || many >= (int)UIT_NONE)
            return UIT_E_NOMEM;
        uit_op_t o = {OP_PLURAL, r.rk, r.ref, r.f, (uint16_t)one, (uint16_t)many};
        return op_add(e, o);
    }
    return uit_check_fail(e, "T01", UIT_MSG("no transform %.*s", "%.*s"), (int)on, w);
}

static int tmpl_body(uit_t *e, const char *src, int model, uit_tmpl_t *T)
{
    size_t n = strlen(src), i = 0;
    // a template's ops are contiguous: its pieces' sub-templates are compiled first
    // into ops of their own, so the pieces' ops are gathered after into a run
    int ops[64];
    int nops = 0, pieces = 0;
    uint8_t one = UIT_TEXT, bad = 0;
    char *lit = UIT_MALLOC(n + 1);
    if (!lit)
        return UIT_E_NOMEM;
    size_t ln = 0;
    while (i < n) {
        char c = src[i];
        if ((c == '{' || c == '}') && i + 1 < n && src[i + 1] == c) {
            lit[ln++] = c;
            i += 2;
            continue;
        }
        if (c == '}') {
            UIT_FREE(lit);
            return uit_check_fail(e, "T01", UIT_MSG("a lone '}'", ""));
        }
        if (c != '{') {
            lit[ln++] = c;
            i++;
            continue;
        }
        size_t k = i + 1;
        int depth = 0;
        while (k < n && (src[k] != '}' || depth)) {
            depth += src[k] == '{' ? 1 : src[k] == '}' ? -1 : 0;
            k++;
        }
        if (k >= n) {
            UIT_FREE(lit);
            return uit_check_fail(e, "T01", UIT_MSG("'{' with no '}'", ""));
        }
        if (ln) {
            int o = lit_add(e, lit, ln);
            if (o < 0 || nops >= 63) {
                UIT_FREE(lit);
                return o < 0 ? o : uit_fail(e, UIT_E_LANG, UIT_MSG("a template of more than 63 pieces", ""));
            }
            ops[nops++] = o;
            ln = 0;
        }
        uint8_t ty = UIT_TEXT;
        int o = piece(e, src + i + 1, k - i - 1, model, T, &ty);
        if (o < 0 || nops >= 63) {
            UIT_FREE(lit);
            return o < 0 ? o : uit_fail(e, UIT_E_LANG, UIT_MSG("a template of more than 63 pieces", ""));
        }
        ops[nops++] = o;
        pieces++;
        one = ty;
        if (!bad && (ty == UIT_BOOL || ty == UIT_MODEL))
            bad = ty;
        i = k + 1;
    }
    if (ln) {
        int o = lit_add(e, lit, ln);
        if (o < 0) {
            UIT_FREE(lit);
            return o;
        }
        ops[nops++] = o;
    }
    UIT_FREE(lit);
    if (nops == 1 && pieces == 1)
        T->type = one;
    else if (bad)
        return uit_check_fail(e, "T03", UIT_MSG("a %s in a text", "%s"), uit_type_name(bad));
    else
        T->type = UIT_TEXT;
    // gather: the template's own ops as one run at the end
    int first = e->nops;
    for (int j = 0; j < nops; j++) {
        uit_op_t o = e->ops[ops[j]];
        if (op_add(e, o) < 0)
            return UIT_E_NOMEM;
    }
    T->code = (uint16_t)first;
    T->ncode = (uint16_t)nops;
    return 0;
}

int uit_tmpl_compile(uit_t *e, const char *src, int model)
{
    uit_tmpl_t T;
    memset(&T, 0, sizeof T);
    T.lit = 1;
    T.type = UIT_TEXT;
    int sat = uit_buf_put(&e->pool, src, strlen(src));
    if (sat < 0 || sat >= (int)UIT_NONE)
        return UIT_E_NOMEM;
    T.src = (uint16_t)sat;
    int r = tmpl_body(e, src, model, &T);
    if (r < 0) {
        char was[160], code[4];
        memcpy(was, e->err, sizeof was);
        memcpy(code, e->code, sizeof code);
        if (r == UIT_E_CHECK)
            return uit_check_fail(e, code, UIT_MSG("template %s: %s", "template %s %s"), src, was);
        return uit_fail(e, r, UIT_MSG("template %s: %s", "template %s %s"), src, was);
    }
    if (uit_grow((void **)&e->tmpls, &e->ctmpls, e->ntmpls + 1, sizeof *e->tmpls))
        return UIT_E_NOMEM;
    e->tmpls[e->ntmpls] = T;
    return e->ntmpls++;
}

static void bytes_text(uit_buf_t *b, int32_t n)
{
    if (n < 1024)
        uit_buf_printf(b, "%ld B", (long)n);
    else if (n < 1024 * 1024)
        uit_buf_printf(b, "%ld KB", (long)((n + 512) / 1024));
    else
        uit_buf_printf(b, "%.1f MB", n / 1048576.0);
}

static void render(uit_t *e, int t, int model, int row)
{
    const uit_tmpl_t *T = &e->tmpls[t];
    uit_buf_t *b = &e->scratch;
    for (int i = 0; i < T->ncode; i++) {
        const uit_op_t *o = &e->ops[T->code + i];
        val_t v;
        switch (o->op) {
        case OP_LIT:
            uit_buf_printf(b, "%.*s", (int)o->d, uit_str(e, o->c));
            break;
        case OP_VAL:
            ref_get(e, o->rk, o->ref, o->f, model, row, &v);
            val_text(b, &v);
            break;
        case OP_COUNT:
            ref_get(e, o->rk, o->ref, o->f, model, row, &v);
            uit_buf_printf(b, "%ld", (long)v.i);
            break;
        case OP_PLURAL:
            ref_get(e, o->rk, o->ref, o->f, model, row, &v);
            uit_buf_printf(b, "%ld %s", (long)v.i, uit_str(e, v.i == 1 ? o->c : o->d));
            break;
        case OP_BYTES:
            ref_get(e, o->rk, o->ref, o->f, model, row, &v);
            bytes_text(b, v.i);
            break;
        case OP_FMT: {
            ref_get(e, o->rk, o->ref, o->f, model, row, &v);
            const char *spec = uit_str(e, o->c);
            char last = (char)o->d;     // the spec's conversion, parsed at compile
            if (last == 's') {
                uit_buf_t tmp = {0};
                val_text(&tmp, &v);
                uit_buf_printf(b, spec, tmp.p ? tmp.p : "");
                uit_buf_free(&tmp);
            } else if (last == 'd' || last == 'i' || last == 'x') {
                uit_buf_printf(b, spec, (int)v.i);
            } else {
                uit_buf_printf(b, spec, v.type == UIT_NUM ? v.d : (double)v.i);
            }
            break;
        }
        case OP_CHOICE:
            render(e, uit_cond_eval(e, o->ref, model, row) ? o->c : o->d, model, row);
            break;
        case OP_MAP: {
            ref_get(e, o->rk, o->ref, o->f, model, row, &v);
            uint32_t at = b->n;
            val_text(b, &v);
            const char *w = uit_label_word(e, o->c, b->p + at, b->n - at);
            b->n = at;
            uit_buf_printf(b, "%s", w);
            break;
        }
        }
    }
}

uint32_t uit_tmpl_render(uit_t *e, int t, int model, int row, uint32_t *n)
{
    uit_buf_t *b = &e->scratch;
    if (!b->p)
        uit_buf_put(b, "", 0);
    uint32_t at = b->n;
    render(e, t, model, row);
    *n = b->n - at;
    uit_buf_put(b, "", 0);      // its NUL, and the next starts after it
    return at;
}

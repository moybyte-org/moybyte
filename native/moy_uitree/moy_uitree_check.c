// The UI API's prop check: a node's prop names, retired names, words and
// value classes against the contract's table (moy_uitree_schema.h). Every
// front calls it at the call that names the prop, so the error carries that
// call's line; the codes and the words of each message are this file's alone.
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "moy_uitree_check.h"
#include "moy_uitree_schema.h"

#define PNAME(P) (UIT_SSTR + (P)->name)
#define PWORDS(P) ((P)->words ? UIT_SSTR + (P)->words : NULL)
#define NKINDS (sizeof UIT_SKINDS / sizeof UIT_SKINDS[0])
#define NRETIRED (sizeof UIT_SRETIRED / sizeof UIT_SRETIRED[0])

static const uit_skind_t *kind_of(const char *kind)
{
    for (size_t i = 0; i < NKINDS; i++)
        if (!strcmp(UIT_SSTR + UIT_SKINDS[i].name, kind))
            return &UIT_SKINDS[i];
    return NULL;
}

static const uit_sprop_t *prop_of(const uit_skind_t *K, const char *prop)
{
    for (int i = 0; i < K->n; i++)
        if (!strcmp(PNAME(&UIT_SPROPS[K->first + i]), prop))
            return &UIT_SPROPS[K->first + i];
    return NULL;
}

static const char *retired(const char *kind, const char *prop)
{
    size_t pn = strlen(prop);
    for (size_t i = 0; i < NRETIRED; i++) {
        const char *r = UIT_SRETIRED[i][0];
        if (strncmp(r, prop, pn))
            continue;
        if (r[pn] == 0 || (r[pn] == '@' && !strcmp(r + pn + 1, kind)))
            return UIT_SRETIRED[i][1];
    }
    return NULL;
}

// difflib's SequenceMatcher(None, a, b).ratio() with no junk (names are short): the
// matching blocks found longest first, the earliest in a then in b on a tie.
static int matched(const char *a, int alo, int ahi, const char *b, int blo, int bhi)
{
    int best = 0, bi = alo, bj = blo;
    for (int i = alo; i < ahi; i++)
        for (int j = blo; j < bhi; j++) {
            int k = 0;
            while (i + k < ahi && j + k < bhi && a[i + k] == b[j + k])
                k++;
            if (k > best)
                best = k, bi = i, bj = j;
        }
    if (!best)
        return 0;
    return best + matched(a, alo, bi, b, blo, bj) + matched(a, bi + best, ahi, b, bj + best, bhi);
}

static int ratio_x1000(const char *a, const char *b)
{
    int la = (int)strlen(a), lb = (int)strlen(b);
    return la + lb ? 2000 * matched(a, 0, la, b, 0, lb) / (la + lb) : 1000;
}

// difflib.get_close_matches(word, the kind's props, 2, 0.6): the best two, a tie to the
// later name, as "'a' or 'b'"; "" where none is near.
static void near(const uit_skind_t *K, const char *word, char *out, size_t n)
{
    const char *pick[2] = {NULL, NULL};
    int score[2] = {-1, -1};
    for (int i = 0; i < K->n; i++) {
        const uit_sprop_t *P = &UIT_SPROPS[K->first + i];
        if (P->flags & UIT_SF_DESIGNED)
            continue;
        int s = ratio_x1000(PNAME(P), word);
        if (s < 600)
            continue;
        for (int k = 0; k < 2; k++)
            if (s > score[k] || (s == score[k] && strcmp(PNAME(P), pick[k]) > 0)) {
                if (k == 0)
                    pick[1] = pick[0], score[1] = score[0];
                pick[k] = PNAME(P), score[k] = s;
                break;
            }
    }
    if (!pick[0])
        snprintf(out, n, "%s", "");
    else if (!pick[1])
        snprintf(out, n, "'%s'", pick[0]);
    else
        snprintf(out, n, "'%s' or '%s'", pick[0], pick[1]);
}

// The props a kind takes, in the table's order: "shown, text, tone, ..."
static size_t takes(const uit_skind_t *K, char *out, size_t n)
{
    size_t at = 0;
    out[0] = 0;
    for (int i = 0; i < K->n && at < n; i++) {
        const uit_sprop_t *P = &UIT_SPROPS[K->first + i];
        if (!(P->flags & UIT_SF_DESIGNED))
            at += (size_t)snprintf(out + at, n - at, "%s%s", at ? ", " : "", PNAME(P));
    }
    return at < n ? at : n - 1;
}

static int has_word(const char *words, const char *w)
{
    size_t wn = strlen(w);
    for (const char *s = words; *s;) {
        const char *e = strchr(s, ',');
        size_t sn = e ? (size_t)(e - s) : strlen(s);
        if (sn == wn && !strncmp(s, w, wn))
            return 1;
        if (!e)
            break;
        s = e + 1;
    }
    return 0;
}

// The value classes a type takes: a bit a UIT_VC_*.
#define VC(c) (1u << UIT_VC_##c)
static unsigned classes(int type)
{
    switch (type) {
    case UIT_T_INT:
    case UIT_T_INT_WORD:
    case UIT_T_INT_NAME:
        return VC(TEXT) | VC(INT);      // an int, or a template of one (or a word)
    case UIT_T_COND:
        return VC(TEXT) | VC(BOOL);
    case UIT_T_BOOL:
        return VC(BOOL);
    case UIT_T_LIST:
    case UIT_T_LIST_TPL:
    case UIT_T_LIST_ACTION:
        return VC(LIST);
    case UIT_T_FN:
        return VC(FN);
    case UIT_T_CONDS:
        return VC(DICT);
    case UIT_T_LIST_CONDS:
        return VC(LIST) | VC(DICT);
    case UIT_T_LIT:
        return ~0u;
    default:
        return VC(TEXT);        // a name, an action or a hook, a key, a table, a word, a text
    }
}

static const char *say(char *out, size_t n, const char *code, const char *what, const char *expected,
                       const char *fix)
{
    snprintf(out, n, "%s\x1f%s\x1f%s\x1f", what, expected, fix);
    return code;
}

static const char *const VC_NAMES[] = {"", "a text", "an int", "a bool", "a list", "a table of names (a dict)",
                                        "a function", "a number"};

// the fix for a value of a class prop `prop` (of type `type`) does not take
static void class_fix(const char *prop, int type, char *fix, size_t n)
{
    if (type == UIT_T_BOOL)
        snprintf(fix, n, "%s=true or %s=false", prop, prop);
    else if (type == UIT_T_TPL)
        snprintf(fix, n, "%s='{NAME}' for a state's value, or a text", prop);
    else if (type == UIT_T_INT)
        snprintf(fix, n, "%s=3, or %s='{NAME}' of an int", prop, prop);
    else
        snprintf(fix, n, "%s=, %s", prop, UIT_STYPES[type - 1]);
}

const char *uit_check_prop(const char *kind, const char *prop, int vc, const char *word, char *out, size_t n)
{
    char what[96], expected[400], fix[sizeof expected + 64];
    int builds = vc & UIT_VC_BUILDS;
    vc &= ~UIT_VC_BUILDS;
    const uit_skind_t *K = kind_of(kind);
    if (!K) {
        snprintf(what, sizeof what, "no kind '%s'", kind);
        return say(out, n, "N01", what, "", "a kind of the UI API (\xc2\xa7" "7)");
    }
    if ((K->flags & UIT_SF_DESIGNED) && !builds) {
        snprintf(what, sizeof what, "ui.%s is designed, not built yet", kind);
        return say(out, n, "N01", what, "", "another kind, until it is built");
    }
    const uit_sprop_t *P = prop_of(K, prop);
    if (!P) {
        const char *why = retired(kind, prop);
        snprintf(what, sizeof what, why ? "ui.%s %s= is retired" : "ui.%s has no prop '%s'", kind, prop);
        if (why)
            return say(out, n, "N04", what, "", why);
        takes(K, expected, sizeof expected);
        char nm[80];
        near(K, prop, nm, sizeof nm);
        if (nm[0])
            snprintf(fix, sizeof fix, "did you mean %s?", nm);
        else
            snprintf(fix, sizeof fix, "drop it; %s takes %s", kind, expected[0] ? expected : "none");
        return say(out, n, "N01", what, expected[0] ? expected : "(none)", fix);
    }
    if ((P->flags & UIT_SF_DESIGNED) && !builds) {
        snprintf(what, sizeof what, "ui.%s has no prop '%s' yet: the spec designs it", kind, prop);
        return say(out, n, "N01", what, "", "drop it, until it is built");
    }
    if (vc != UIT_VC_ANY && vc < (int)(sizeof VC_NAMES / sizeof VC_NAMES[0]) && !(classes(P->type) & (1u << vc))) {
        snprintf(what, sizeof what, "ui.%s %s= is %s", kind, prop, VC_NAMES[vc]);
        class_fix(prop, P->type, fix, sizeof fix);
        return say(out, n, P->type == UIT_T_INT ? "T03" : "N01", what, UIT_STYPES[P->type - 1], fix);
    }
    if (P->type == UIT_T_INT && vc == UIT_VC_TEXT && word && !strchr(word, '{')) {
        snprintf(what, sizeof what, "ui.%s %s='%s' is a text, not an int", kind, prop, word);
        class_fix(prop, P->type, fix, sizeof fix);
        return say(out, n, "N01", what, "an int, or '{NAME}' of an int", fix);
    }
    const char *words = PWORDS(P);
    if (words && word && word[0] != '{' && !has_word(words, word)) {
        snprintf(what, sizeof what, "ui.%s %s='%s'", kind, prop, word);
        size_t i = 0;
        for (const char *s = words; *s && i + 2 < sizeof expected; s++) {
            expected[i++] = *s;
            if (*s == ',')
                expected[i++] = ' ';
        }
        expected[i] = 0;
        const char *e = strchr(words, ',');
        snprintf(fix, sizeof fix, "%s='%.*s'", prop, e ? (int)(e - words) : (int)strlen(words), words);
        return say(out, n, "N01", what, expected, fix);
    }
    return NULL;
}

const char *uit_prop_words(const char *kind, const char *prop)
{
    const uit_skind_t *K = kind_of(kind);
    const uit_sprop_t *P = K ? prop_of(K, prop) : NULL;
    return P ? PWORDS(P) : NULL;
}

const char *uit_prop_type(const char *kind, const char *prop)
{
    const uit_skind_t *K = kind_of(kind);
    const uit_sprop_t *P = K ? prop_of(K, prop) : NULL;
    return P ? UIT_SKEYS[P->type - 1] : NULL;
}

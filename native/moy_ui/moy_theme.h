// moy_theme: the theme loader (docs/theming_2026-09.md §3-§5, §7). A theme
// is a theme.json, untrusted data: this file parses it with a strict JSON
// reader of its own, refuses what it cannot read, clamps and drops what it
// can, and flattens a theme into the live token row the toolkit's looks
// resolve against (moy_ui_look_set) and native/moy_app publishes.
//
// Format 1 defines `format`, `name`, `inherits`, `colors.dark` /
// `colors.light` (role -> MOY64 index, or a flag role -> true/false; a role
// is a token of the live row or a content role), `metrics` (theme-wide
// numbers, each clamped to its range) and `text` (part -> state -> a text
// effect and the role it paints with). The keys style.json's theme section
// lists as reserved are accepted and not yet read; any other key is ignored
// and noted. A reader refuses a newer `format`; a key a later phase defines
// is additive and needs no bump.
//
// Resolution walks `inherits` from the theme to the default seed (a theme
// that names no parent inherits the default), each role taken from the
// nearest package that sets it, then fills what is still unset from the
// derivation rules, so every role of the row is set; metrics and text
// effects resolve the same way, nearest package first, the default seed
// setting every metric. The chain is capped at
// MOY_THEME_DEPTH and a cycle is refused. Readability is a report, never a
// refusal: each (background, ink) pair under MOY_THEME_CONTRAST sets its bit.
//
// The seeds are style.json's, embedded in moy_ui_tables.c. One more package
// can be installed beside them (the one loaded theme of the memory rule); it
// may not take a seed's name.

#ifndef MOY_THEME_H
#define MOY_THEME_H

#include <stddef.h>
#include <stdint.h>

#include "moy_ui_tables.h"

// What a parse or an install answers: OK, or why the theme was refused.
enum {
    MOY_THEME_OK = 0,
    MOY_THEME_BIG = 1,        // over MOY_THEME_BYTES
    MOY_THEME_SYNTAX = 2,     // not JSON this reader takes, or nested too deep
    MOY_THEME_NEWER = 3,      // `format` missing, not an integer, or newer than ours
    MOY_THEME_NAME = 4,       // `name` missing, too long, or outside [a-z0-9_-]
    MOY_THEME_TAKEN = 5,      // an install under a seed's name
    MOY_THEME_CHAIN = 6,      // `inherits` names no package, cycles, or runs too deep
    MOY_THEME_NOMEM = 7,
};

// What a parse noted and got past (a bit set each).
enum {
    MOY_THEME_N_CLAMPED = 1,  // an index outside 0..63, clamped
    MOY_THEME_N_UNKNOWN = 2,  // a key or role this format does not define, ignored
    MOY_THEME_N_DROPPED = 4,  // a value of the wrong type, dropped
};

#define MOY_THEME_NAME_MAX 24
#define MOY_THEME_UNSET (-1)
#define MOY_THEME_NO_METRIC INT8_MIN
#define MOY_THEME_NO_ROLE 0xff

enum { MOY_THEME_DARK = 0, MOY_THEME_LIGHT = 1 };

typedef struct {
    char name[MOY_THEME_NAME_MAX + 1];
    char inherits[MOY_THEME_NAME_MAX + 1];  // "" : the default
    int8_t role[2][MOY_THEME_ROLES];        // [variant][role], MOY_THEME_UNSET where silent
    int8_t metric[MOY_THEME_METRICS];       // MOY_THEME_NO_METRIC where silent
    uint8_t effect[MOY_THEME_EFFECTS_CAP][4];   // kind, state, effect, role
    uint8_t n_effects;
    uint8_t notes;
} moy_theme_pkg_t;

// Parse one theme.json into `out` (no inheritance). MOY_THEME_OK or a refusal.
int moy_theme_parse(const uint8_t *src, size_t n, moy_theme_pkg_t *out);

// The seeds, in style.json's order; seed 0 is the default.
int moy_theme_count(void);
const char *moy_theme_name(int i);

// Install `src` as the one package beside the seeds, replacing the last; its
// chain is resolved once here. A refusal leaves the installed package as it
// was. *notes gets the parse's notes.
int moy_theme_install(const uint8_t *src, size_t n, uint8_t *notes);
// The installed package's name, or NULL.
const char *moy_theme_installed(void);

// The role row of theme `name` in `variant` (the MOY_UI_TOKENS tokens, then
// the content roles): every role set (a flag 0 or 1).
// An unknown name resolves as the default. *unreadable gets one bit per pair
// of style.json's theme.pairs that misses the contrast floor. MOY_THEME_OK, or
// MOY_THEME_CHAIN / MOY_THEME_NOMEM with the default's row written instead.
int moy_theme_resolve(const char *name, size_t n, int variant,
                      int32_t row[MOY_THEME_ROLES], uint32_t *unreadable);

// Theme `name`'s metrics (every one set) and its text effects:
// effects[kind][state] = {effect, role}, {0, MOY_THEME_NO_ROLE} where none.
// The return is as resolve's.
int moy_theme_shape(const char *name, size_t n, int8_t metrics[MOY_THEME_METRICS],
                    uint8_t effects[MOY_UI_KINDS][MOY_UI_STATES][2]);

#endif // MOY_THEME_H

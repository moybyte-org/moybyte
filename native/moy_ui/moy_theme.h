// moy_theme: the theme loader (docs/theming_2026-09.md §3-§5, §7). A theme
// is a theme.json, untrusted data: this file parses it with a strict JSON
// reader of its own, refuses what it cannot read, clamps and drops what it
// can, and flattens a theme into the live token row the toolkit's looks
// resolve against (moy_ui_look_set) and native/moy_app publishes.
//
// Format 1 defines `format`, `name`, `inherits`, `colors.dark` /
// `colors.light` (role -> MOY64 index, or a flag role -> true/false; a role
// is a token of the live row or a content role), `metrics` (theme-wide
// numbers, each clamped to its range), `text` (part -> state -> a text
// effect and the role it paints with) and `surfaces` (part -> state -> one
// line of the surface grammar, compiled here into a record the painter
// draws), `frame` (the window frame's style, title alignment and button
// order: moy_ui.h's moy_ui_frame), `fonts` (font role -> a font file of
// the package, read by the font loader, moy_text.h) and `layout` (its `bars`:
// docs/shell_layout_2026-10.md §5, each capped and clamped here and the
// required widgets checked on the data). The keys style.json's theme section
// lists as reserved are accepted and not yet read; any other key is ignored
// and noted. A reader refuses a newer `format`; a key a later phase defines
// is additive and needs no bump.
//
// Resolution walks `inherits` from the theme to the default seed (a theme
// that names no parent inherits the default), each role taken from the
// nearest package that sets it, then fills what is still unset from the
// derivation rules, so every role of the row is set; metrics, text
// effects and surfaces resolve the same way, nearest package first, the
// default seed setting every metric. A layout resolves whole, from the
// nearest package that sets one; a bar list is never merged. The chain is capped at
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

#include "moy_ui.h"
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

// A layout's state: no `bars` (it inherits), bars, or bars the required-set
// rule refused (the default's stand in at resolve).
enum { MOY_THEME_LAYOUT_SILENT = 0, MOY_THEME_LAYOUT_SET = 1, MOY_THEME_LAYOUT_REFUSED = 2 };

// One bar: its edge, its size in logical pixels and its slots' widgets
// (left, center, right), each an index of moy_theme_widget_names. Its `text`
// widgets' strings are the layout's text[text0 .. text0 + n_text), in slot order.
typedef struct {
    uint8_t edge;                                    // index of moy_theme_bar_edges
    uint8_t size;
    uint8_t n[MOY_THEME_SLOTS];
    uint8_t w[MOY_THEME_SLOTS][MOY_THEME_SLOT_CAP];
    uint8_t text0, n_text;
} moy_theme_bar_t;

typedef struct {
    uint8_t set;                                     // MOY_THEME_LAYOUT_*
    uint8_t n_bars;
    moy_theme_bar_t bar[MOY_THEME_BARS];             // in the package's order
    uint8_t n_text;
    char text[MOY_THEME_WIDGET_CAP][MOY_THEME_TEXT_MAX + 1];
} moy_theme_layout_t;

typedef struct {
    char name[MOY_THEME_NAME_MAX + 1];
    char inherits[MOY_THEME_NAME_MAX + 1];  // "" : the default
    int8_t role[2][MOY_THEME_ROLES];        // [variant][role], MOY_THEME_UNSET where silent
    int8_t metric[MOY_THEME_METRICS];       // MOY_THEME_NO_METRIC where silent
    uint8_t effect[MOY_THEME_EFFECTS_CAP][4];   // part, state, effect, role
    uint8_t n_effects;
    uint8_t surface[MOY_THEME_SURFACES_CAP][2]; // part, state
    moy_ui_surface_t surface_rec[MOY_THEME_SURFACES_CAP];
    uint8_t n_surfaces;
    uint8_t frame[MOY_UI_FRAME_FIELDS];     // moy_ui_shape_t's frame, 0 where silent
    char font[MOY_THEME_FONT_ROLES][MOY_THEME_NAME_MAX + 1];  // a file per role, "" where silent
    moy_theme_layout_t layout;
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
// Drop the installed package: only the seeds remain.
void moy_theme_uninstall(void);

// The role row of theme `name` in `variant` (the MOY_UI_TOKENS tokens, then
// the content roles): every role set (a flag 0 or 1).
// An unknown name resolves as the default. *unreadable gets one bit per pair
// of style.json's theme.pairs that misses the contrast floor. MOY_THEME_OK, or
// MOY_THEME_CHAIN / MOY_THEME_NOMEM with the default's row written instead.
int moy_theme_resolve(const char *name, size_t n, int variant,
                      int32_t row[MOY_THEME_ROLES], uint32_t *unreadable);

// Theme `name`'s shape: its metrics (every one set), its text effects and its
// surfaces, each from the nearest package that sets it (moy_ui.h's
// moy_ui_shape_t). The return is as resolve's.
int moy_theme_shape(const char *name, size_t n, moy_ui_shape_t *out);

// Theme `name`'s font files, one per role of moy_theme_font_roles, each from
// the nearest package that names one ("" where none does). The return is as
// resolve's.
int moy_theme_fonts(const char *name, size_t n,
                    char out[MOY_THEME_FONT_ROLES][MOY_THEME_NAME_MAX + 1]);

// Theme `name`'s layout, whole, from the nearest package that sets one.
// Where that one was refused by the required-set rule, or the chain does not
// resolve, the default seed's stands in and out->set is
// MOY_THEME_LAYOUT_REFUSED; MOY_THEME_LAYOUT_SILENT (no bars) when the
// default sets none. The return is as resolve's.
int moy_theme_layout(const char *name, size_t n, moy_theme_layout_t *out);
// Bars, slots, slot cap, widget cap, text max, widgets, sizeof(moy_theme_layout_t):
// the bindings' layout check.
extern const int moy_theme_layout_dims[7];

// A seed package's font file by name (the tables embed every file a seed's
// `fonts` names): its bytes and *len, or NULL.
const uint8_t *moy_theme_font_file(const char *name, size_t n, size_t *len);

// Compile one surface's text (docs/theming_2026-09.md §4.4) into *out.
// MOY_THEME_OK, or -1 for text the grammar does not take; *notes gets
// MOY_THEME_N_CLAMPED for an index clamped to 0..63.
int moy_theme_surface(const char *s, size_t n, moy_ui_surface_t *out, uint8_t *notes);

#endif // MOY_THEME_H

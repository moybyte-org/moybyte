// moy_theme: the theme loader (docs/theming_2026-09.md §3-§5, §7). A theme
// is a theme.json, untrusted data: this file parses it with a strict JSON
// reader of its own, refuses what it cannot read, clamps and drops what it
// can, and flattens a theme into the live token row the toolkit's looks
// resolve against (moy_ui_look_set) and native/moy_app publishes.
//
// Format 2 defines `format`, `name`, `inherits`, `colors.dark` /
// `colors.light` (role -> a MOY64 index or "#rrggbb", or a flag role ->
// true/false; a role is a token of the live row or a content role), `look`
// (the theme model's fields, docs/media/look_2026-10/THEMES.md: shared, then
// a `dark` and a `light` object of their own), `metrics` (theme-wide
// numbers, each clamped to its range), `text` (part -> state -> a text
// effect and the role it paints with) and `surfaces` (part -> state -> one
// line of the surface grammar, compiled here into a record the painter
// draws), `frame` (the window frame's style, title alignment and button
// order: moy_ui.h's moy_ui_frame), `fonts` (font role -> a font file of
// the package, read by the font loader, moy_text.h), `sprites` and `icons`
// (a .moygfx file of the package each: the role sheet the primitives draw
// sprites from, and the bar's icon sheet) and `layout` (its `bars`:
// docs/shell_layout_2026-10.md §5, each capped and clamped here and the
// required widgets checked on the data). The keys style.json's theme section
// lists as reserved are accepted and not yet read; any other key is ignored
// and noted. A reader refuses any `format` but its own (a format-1 package
// reads as absent: no migration); a key a later phase defines is additive and
// needs no bump.
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
    MOY_THEME_NAME = 4,       // `name` missing, too long, outside [a-z0-9_-], or not
                              // starting with a letter or a digit
    MOY_THEME_TAKEN = 5,      // an install under a seed's name
    MOY_THEME_CHAIN = 6,      // `inherits` names no package, cycles, or runs too deep
    MOY_THEME_NOMEM = 7,
};

// What a parse noted and got past (a bit set each).
enum {
    MOY_THEME_N_CLAMPED = 1,  // an index outside 0..63 or a number out of range, clamped
    MOY_THEME_N_UNKNOWN = 2,  // a key or role this format does not define, ignored
    MOY_THEME_N_DROPPED = 4,  // a value of the wrong type, dropped
};

#define MOY_THEME_NAME_MAX 24
#define MOY_THEME_UNSET (-1)

// A colour value (a role's, a look leaf's): a MOY64 index 0..63, a block slot
// 64..127 (moy_theme_place's answer only), or RGB: MOY_THEME_RGB | 0xrrggbb.
// A paint leaf may also be MOY_THEME_PAINT | k (paint k of its look) or
// MOY_THEME_VIBRANCY. Any leaf may be MOY_THEME_NONE (silent: the renderer
// derives it) or MOY_THEME_OFF (`null`: explicitly none).
#define MOY_THEME_RGB 0x1000000
#define MOY_THEME_PAINT 0x2000000
#define MOY_THEME_VIBRANCY 0x3000000
#define MOY_THEME_NONE INT32_MIN
#define MOY_THEME_OFF (INT32_MIN + 1)
#define MOY_THEME_IS_RGB(v) ((v) >= MOY_THEME_RGB && (v) < MOY_THEME_PAINT)
#define MOY_THEME_SLOT0 64      // the shell's block: slots 64..127
#define MOY_THEME_BLOCK 64
#define MOY_THEME_LOOK_TEXTS 4  // the text leaves' strings a look holds
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
    uint8_t home;                                    // index of moy_theme_home_names
} moy_theme_layout_t;

// A paint (THEMES.md): stops (t in thousandths, a colour) and a mode of
// moy_theme_paint_modes, `steps` its band count.
typedef struct {
    uint8_t n, mode, steps, pad;
    int16_t t[MOY_THEME_PAINT_STOPS];
    int32_t c[MOY_THEME_PAINT_STOPS];
} moy_theme_paint_t;

// A backdrop shape (docs/media/look_2026-10/GAPS.md gap 9): a kind of
// moy_theme_bd_kinds, its coordinates in thousandths of the surface (a wave's
// as x, y pairs), a paint of up to MOY_THEME_BD_STOPS stops and an alpha.
typedef struct {
    uint8_t kind, mode, steps, alpha, n_stops, n_at;
    int16_t t[MOY_THEME_BD_STOPS];
    int32_t c[MOY_THEME_BD_STOPS];
    int16_t at[MOY_THEME_BD_AT];
} moy_theme_shape_t;

#define MOY_THEME_BD_SILENT 0xff   // a list no section sets

// A package's look: what it sets, leaf by leaf, in a section (0 shared,
// 1 `dark`, 2 `light`); its tiles (app -> colour) and app_buttons (app and
// kind -> a button's leaves, MOY_THEME_NONE where silent).
typedef struct {
    uint16_t leaf;
    uint8_t section, pad;
    int32_t v;
} moy_theme_entry_t;

typedef struct {
    char name[MOY_THEME_MAPNAME_MAX + 1];
    uint8_t section, kind;
    int32_t v[MOY_THEME_BTN_LEAVES];        // a tile: v[0] its colour
} moy_theme_mapent_t;

typedef struct {
    int32_t v[MOY_THEME_LEAVES];
    uint8_t n_paints, n_tiles, n_appbtn, n_text;
    moy_theme_paint_t paint[MOY_THEME_PAINTS_CAP];
    moy_theme_mapent_t tile[MOY_THEME_TILES_CAP];
    moy_theme_mapent_t appbtn[MOY_THEME_APPBTN_CAP];
    char text[MOY_THEME_LOOK_TEXTS][MOY_THEME_LOOK_TEXT_MAX + 1];  // a text leaf: index + 1
} moy_theme_look_t;

typedef struct {
    char name[MOY_THEME_NAME_MAX + 1];
    char inherits[MOY_THEME_NAME_MAX + 1];  // "" : the default
    int32_t role[2][MOY_THEME_ROLES];       // [variant][role], MOY_THEME_UNSET where silent
    moy_theme_entry_t entry[MOY_THEME_ENTRIES_CAP];
    uint16_t n_entries;
    uint8_t n_paints, n_tiles, n_appbtn, n_text;
    moy_theme_paint_t paint[MOY_THEME_PAINTS_CAP];
    moy_theme_mapent_t tile[MOY_THEME_TILES_CAP];
    moy_theme_mapent_t appbtn[MOY_THEME_APPBTN_CAP];
    char text[MOY_THEME_LOOK_TEXTS][MOY_THEME_LOOK_TEXT_MAX + 1];
    moy_theme_shape_t bd[MOY_THEME_BD_POOL];
    uint8_t n_bd;
    // [section][surface]: the list's first shape in bd and its count, the
    // count MOY_THEME_BD_SILENT where the section is silent (0: no backdrop).
    uint8_t bd_at[3][MOY_THEME_BD_SURFACES], bd_n[3][MOY_THEME_BD_SURFACES];
    int8_t metric[MOY_THEME_METRICS];       // MOY_THEME_NO_METRIC where silent
    uint8_t effect[MOY_THEME_EFFECTS_CAP][4];   // part, state, effect, role
    uint8_t n_effects;
    uint8_t surface[MOY_THEME_SURFACES_CAP][2]; // part, state
    moy_ui_surface_t surface_rec[MOY_THEME_SURFACES_CAP];
    uint8_t n_surfaces;
    uint8_t frame[MOY_UI_FRAME_FIELDS];     // moy_ui_shape_t's frame, 0 where silent
    char font[MOY_THEME_FONT_ROLES][MOY_THEME_NAME_MAX + 1];  // a file per role, "" where silent
    char sheet[MOY_THEME_SHEETS][MOY_THEME_NAME_MAX + 1];     // a .moygfx file, "" where silent
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

// Theme `name`'s look in `variant`: each leaf from the nearest package that
// sets it, its variant's object before its shared one; with `defaults`, a
// leaf still silent takes the model's default. The return is as resolve's.
int moy_theme_look(const char *name, size_t n, int variant, int defaults,
                   moy_theme_look_t *out);
extern const size_t moy_theme_look_bytes;

// Theme `name`'s backdrop for `surface` (an index of moy_theme_bd_surfaces)
// in `variant`: the nearest package's list, its variant's before its shared
// one, as the records mg_backdrop (native/moy_gfx/moy_backdrop.h) draws, each
// colour as 0xrrggbb. The bytes written (0: no backdrop, the surface is its
// `desktop` fill), or -1 where `cap` is short.
int moy_theme_backdrop(const char *name, size_t n, int variant, int surface,
                       uint8_t *out, size_t cap);
// The most bytes moy_theme_backdrop writes for one surface.
#define MOY_THEME_BD_BYTES (MOY_THEME_BD_SHAPES * (6 + 5 * MOY_THEME_BD_STOPS + 2 * MOY_THEME_BD_AT))
// Leaves, paint stops, paints, tiles, app_buttons, a button's leaves, a map
// name's and a text's longest, texts, the first button leaf: the bindings' check.
extern const int moy_theme_look_dims[10];

// -- RGB (docs/look_2026-10.md step 5) ------------------------------------------
//
// A colour value's RGB565 word (canonical order), its WCAG luminance (x10000,
// moy_theme_lum's scale, on the RGB565 it shows as) and its nearest MOY64
// index (what theme(), a compiled app and a cart canvas are given). A slot
// reads the live block (moy_theme_block_use).
uint16_t moy_theme_rgb565(int32_t v);
int32_t moy_theme_lum_of(int32_t v);
int32_t moy_theme_nearest(int32_t v);
// Place `n` colour values into a block: an index stays, an RGB value takes
// the slot of its RGB565 word (64 + its place in `block`, a new word the next
// free one; past MOY_THEME_BLOCK, its nearest index). out[i] is the placed
// value, near[i] the nearest index of each; anything else is copied. The
// words used.
int moy_theme_place(const int32_t *in, int n, int32_t *out, int32_t *near,
                    uint16_t block[MOY_THEME_BLOCK]);
// The live theme's block, which moy_theme_lum_of reads a slot through.
void moy_theme_block_use(const uint16_t block[MOY_THEME_BLOCK], int used);

// Theme `name`'s shape: its metrics (every one set), its text effects and its
// surfaces, each from the nearest package that sets it (moy_ui.h's
// moy_ui_shape_t). The return is as resolve's.
int moy_theme_shape(const char *name, size_t n, moy_ui_shape_t *out);

// Theme `name`'s font files, one per role of moy_theme_font_roles, each from
// the nearest package that names one ("" where none does). The return is as
// resolve's.
int moy_theme_fonts(const char *name, size_t n,
                    char out[MOY_THEME_FONT_ROLES][MOY_THEME_NAME_MAX + 1]);

// Theme `name`'s sheet files, one per key of moy_theme_sheet_keys
// (`sprites`, `icons`), each from the nearest package that names one ("" where
// none does). The return is as resolve's.
int moy_theme_sheets(const char *name, size_t n,
                     char out[MOY_THEME_SHEETS][MOY_THEME_NAME_MAX + 1]);

// -- the sprite sheet (docs/theming_2026-09.md §4.6) ------------------------------
//
// A sheet is user data in the cart sheet's hex format: a line is a row of up
// to MOY_THEME_SHEET_W hex digits, one nibble a pixel (a short line's rest is
// slot 0), blank lines skipped, spaces, tabs and a CR at a line's ends
// ignored. Anything else is refused: MOY_THEME_SYNTAX for a byte that is not a
// hex digit or a line over the width, MOY_THEME_BIG for more than `rows_max`
// rows or more than MOY_THEME_SHEET_BYTES(rows_max) bytes. *rows gets the rows.
int moy_theme_sheet_check(const uint8_t *src, size_t n, int rows_max, int *rows);
// Load the theme's ROLE sheet (`sprites`): checked against
// MOY_THEME_SPRITES_MAX_ROWS, its first MOY_THEME_SPRITE_ROWS rows decoded into
// a buffer the loader owns, and each cell of moy_theme_cells marked set when a
// pixel in it is not slot 0. A refusal loads nothing and leaves no sheet.
int moy_theme_sheet_load(const uint8_t *src, size_t n);
void moy_theme_sheet_clear(void);
// Width, sprite rows, icon rows, the rows kept, cells, sheets: the bindings' check.
extern const int moy_theme_sheet_dims[6];
// The loaded sheet's slots (MOY_THEME_SHEET_W a row, MOY_THEME_SPRITE_ROWS
// rows), or NULL when none is loaded.
const uint8_t *moy_theme_sheet(void);
// The cell `part` draws in `state` (and title button `sub`, 0 elsewhere): the
// set cell of that state, else of the state style.json's theme.chain reaches
// first; -1 where none is set or no sheet is loaded.
int moy_theme_sheet_cell(int part, int state, int sub);

// Theme `name`'s layout, whole, from the nearest package that sets one.
// Where that one was refused by the required-set rule, or the chain does not
// resolve, the default seed's stands in and out->set is
// MOY_THEME_LAYOUT_REFUSED; MOY_THEME_LAYOUT_SILENT (no bars) when the
// default sets none. The return is as resolve's.
int moy_theme_layout(const char *name, size_t n, moy_theme_layout_t *out);
// Bars, slots, slot cap, widget cap, text max, widgets, sizeof(moy_theme_layout_t):
// the bindings' layout check.
extern const int moy_theme_layout_dims[7];

// Resolve a stored package from its bytes without installing it (the
// Appearance cards, a layout row naming a package, a paste's preview): parse
// `src` into the caller's `buf` (moy_theme_pkg_bytes) and, on MOY_THEME_OK,
// make it the PROBE, which the calls above resolve by its name ahead of the
// installed package until moy_theme_probe_end. A stored package is standalone:
// one naming `inherits` is refused (MOY_THEME_CHAIN), as is a seed's name
// (MOY_THEME_TAKEN). *notes gets the parse's notes.
int moy_theme_probe(const uint8_t *src, size_t n, moy_theme_pkg_t *buf, uint8_t *notes);
void moy_theme_probe_end(void);
// The probe's name, and the font file it names itself for role i of
// moy_theme_font_roles (its folder provides it): NULL where none.
const char *moy_theme_probe_name(void);
const char *moy_theme_probe_font(int role);
const char *moy_theme_probe_sheet(int key);
extern const size_t moy_theme_pkg_bytes;

// Accent and tint (docs/theming_2026-09.md §4.10): every colour role of a
// source's two resolved rows (moy_theme_resolve's, [MOY_THEME_DARK] and
// [MOY_THEME_LIGHT]) derived from an accent, an index of moy_theme_accents or
// -1 for the source's own, and a tint, an index of moy_theme_tints or -1 for
// none, readable by construction: no pair of theme.pairs under the floor
// where the source's own pairs clear. Integer only, on moy_theme_lum and the
// loader's predicate. Tint and accent -1 answer the source's rows where they
// are readable.
void moy_theme_derive(const int32_t src[2][MOY_THEME_ROLES], int accent, int tint,
                      int32_t out[2][MOY_THEME_ROLES]);

// The theme a change in Appearance makes (§4.10, phase 6 step 5).
#define MOY_THEME_CUSTOM "custom"

// The bytes of `custom`'s theme.json, derived from the package `src` (a
// stored package's theme.json, standalone, or a seed's, moy_theme_seed_src):
// both variants' rows from moy_theme_derive written whole, the source's chain
// flattened into the rest (its metrics, text effects, surfaces, frame, fonts
// and layout, each from the nearest package that sets it, the default seed's
// own only where it is the source, which every package resolves through
// anyway), the source's own `wallpapers` and `images`, `name` custom, no
// `inherits`, minified. Over MOY_THEME_BYTES, the roles whose value the
// resolution (§5) gives back are left out. `scratch` holds two packages.
// MOY_THEME_OK with *len bytes in `out`; a parse's refusal, MOY_THEME_TAKEN for
// a seed's name on bytes that are not that seed's, MOY_THEME_CHAIN for a
// stored package naming `inherits`, or MOY_THEME_BIG (*len the size it needed)
// where it does not fit `cap` or MOY_THEME_BYTES, or the flattened chain holds
// more text effects or surfaces than a package may.
int moy_theme_custom(const uint8_t *src, size_t n, int accent, int tint,
                     moy_theme_pkg_t scratch[2], uint8_t *out, size_t cap, size_t *len);

// Accents, tints, the longest ramp, MOY_THEME_BYTES: the bindings' table check.
extern const int moy_theme_custom_dims[4];

// A seed package's file by name (the tables embed every font a seed's `fonts`
// names, and each font X.fnt's licence text X.txt): its bytes and *len, or NULL.
const uint8_t *moy_theme_font_file(const char *name, size_t n, size_t *len);

// Compile one surface's text (docs/theming_2026-09.md §4.4) into *out.
// MOY_THEME_OK, or -1 for text the grammar does not take; *notes gets
// MOY_THEME_N_CLAMPED for an index clamped to 0..63.
int moy_theme_surface(const char *s, size_t n, moy_ui_surface_t *out, uint8_t *notes);

#endif // MOY_THEME_H

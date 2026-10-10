// moy_chrome: the chrome drawn over a cart and the shell's bar, one body each
// (docs/kernel_cartpath_2026-10.md §3.5): the strip (the crash and tool bars,
// the band and right zone every bar shares), the hold-to-exit pill, the error
// and notice panel, the system menu and its ABOUT box, the notice banner and
// the achievement toast.
//
// A PIECE IS A DISPLAY LIST. Each builder lays its piece out as a short list
// of the canvas verbs it is made of -- rect, rectb, print, a 12x12 glyph, a
// bar icon -- in palette indices, so the one body is drawn two ways: replayed
// through a VM's canvas (runtime/chrome.py's `replay`; the canvas keeps its
// damage, its sprite-batch order and a web view's recording), or rasterised
// here into an RGB565 buffer by the same kernels the canvas verbs end in
// (moy_gfx_kernels.h), for a frame the kernel draws with no VM call. The two
// agree pixel for pixel (tests/test_moy_chrome.py).
//
// THE INKS are the shell's: its theme's roles and its named palette, resolved
// to indices by the shell and handed over (moy_chrome_set_inks) whenever it
// draws, so the kernel's own frames use the theme the shell last drew with.
//
// THE KERNEL'S STATE: the notice banner and the toast with their deadlines
// (one each; a second replaces the first), and the hold-to-exit pill's
// progress, which the kernel's run reads to draw over its frame.

#ifndef MOY_CHROME_H
#define MOY_CHROME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MOY_CHROME_OPS 128
#define MOY_CHROME_TEXT 1536

enum { MOY_CH_RECT = 1, MOY_CH_RECTB = 2, MOY_CH_TEXT = 3, MOY_CH_GLYPH = 4, MOY_CH_ICON = 5 };

typedef struct {
    uint8_t op;
    uint8_t c;                  // palette index (TEXT, RECT, RECTB, GLYPH)
    uint8_t scale;              // TEXT: its scale; GLYPH/ICON: 0 is the canvas's own
    uint8_t pad;
    int16_t x, y, w, h;         // GLYPH: its rect; ICON: x, y
    uint16_t s, n;              // TEXT: the string; GLYPH/ICON: the kind's name
} moy_chrome_op_t;

typedef struct {
    uint16_t n, tn;
    bool full;                  // a piece outgrew the list: it is cut, not wrong
    moy_chrome_op_t op[MOY_CHROME_OPS];
    char text[MOY_CHROME_TEXT];
} moy_chrome_list_t;

// The inks, in this order: the named palette the chrome uses, then the
// theme's roles, then the system menu's row colours (-1: no field).
enum {
    MOY_INK_BLACK, MOY_INK_WHITE, MOY_INK_RED, MOY_INK_ORANGE, MOY_INK_YELLOW,
    MOY_INK_PEACH, MOY_INK_DARK_BLUE, MOY_INK_DARK_PURPLE, MOY_INK_LIGHT_GREY,
    MOY_INK_DARK_GREY,
    MOY_INK_BAR, MOY_INK_BAR_EDGE, MOY_INK_CHROME, MOY_INK_CHROME_DIM, MOY_INK_SURFACE,
    MOY_INK_BORDER, MOY_INK_INK, MOY_INK_INK_DIM, MOY_INK_PLAY, MOY_INK_PANEL, MOY_INK_EDGE,
    MOY_INK_MENU_FIELD_ON, MOY_INK_MENU_INK_ON, MOY_INK_MENU_FIELD_OFF, MOY_INK_MENU_INK_OFF,
    MOY_INK_MENU_HEADER,
    MOY_INKS,
};

// The theme: the inks above, whether its bar is light, the dialog's ring
// count (a skin metric).
void moy_chrome_set_inks(const int16_t inks[MOY_INKS], bool bar_light, int dialog_rings);

void moy_chrome_clear(moy_chrome_list_t *l);

// -- the pieces -----------------------------------------------------------------

// The hold-to-exit pill over a `cw` x `ch` canvas, `held_ms` into a `hold_ms` hold.
void moy_chrome_pill(moy_chrome_list_t *l, int cw, int ch, uint32_t held_ms, uint32_t hold_ms);
// The panel a crashed or refused run leaves: `notice` is the calmer one (a
// cart this console cannot hold), titled `title` ("" or NULL: the table's
// default for its kind); `text` word-wrapped; the hint names the way out (a
// compiled cart has no source to show).
void moy_chrome_panel(moy_chrome_list_t *l, int cw, int ch, bool notice, const char *title,
                      const char *text, bool compiled);

// THE PANELS' WORDS (#143): every word the error, fit and newer-console
// panels say, in one table (moy_chrome.c's SAY), whichever tier draws them,
// under docs/os_voice_v1.md: a dead end says what happened and the next move,
// never in red, and the cart's own error text stays exact under it.
enum {
    MOY_SAY_CRASH = 0,          // a raise, on line N when there is one
    MOY_SAY_STUCK = 1,          // the runaway watch ended a frame
    MOY_SAY_FIT = 2,            // a compiled cart too big for what is free
    MOY_SAY_NEWER = 3,          // a compiled cart built for a newer console
    MOY_SAY_NOLOAD = 4,         // a cart whose files will not load
    MOY_SAY_CONSOLE = 5,        // a stopped run that needs the console to go on
    MOY_SAYS = 6,
};
// The panel's title for `say`, naming `line` where it is above 0 (a raise
// and a stuck frame). snprintf's answer.
int moy_chrome_title(char *out, size_t n, int say, int line);
// The title of the panel over a cart's error `text`: STUCK's when the text
// is the runaway watch's (moy_chrome_stuck_text), else CRASH's.
int moy_chrome_crash_title(char *out, size_t n, const char *text, int line);
// The error a frame the runaway watch ended carries, its budget in ms; a Lua
// run's has its `chunk:N:` position before it.
int moy_chrome_stuck_text(char *out, size_t n, uint32_t budget_ms);
// The fit notice: `title` needs (total, block) bytes where (free, largest)
// are free; total 0 when the need could not be read. Megabytes to one
// decimal, the need rounded up and what is free rounded down, so a refusal
// never reads as a fit.
int moy_chrome_fit_text(char *out, size_t n, const char *title, uint32_t total,
                        uint32_t block, uint32_t free_, uint32_t largest);
// The newer-console notice: `missing`, the imports this console's table
// lacks, comma-separated.
int moy_chrome_newer_text(char *out, size_t n, const char *title, const char *missing);
// The stopped run's own two refusals' texts (NOLOAD, CONSOLE).
const char *moy_chrome_say_text(int say);
// The achievement toast: `title` with the badge's `glyph`.
void moy_chrome_toast(moy_chrome_list_t *l, const char *title, const char *glyph);
// The notice banner, sized off the layout: its width `lw`, font scale `fs`
// and bar height `status_h`; `ok` picks the accent.
void moy_chrome_banner(moy_chrome_list_t *l, int lw, int fs, int status_h, const char *title,
                       const char *sub, bool ok);

// The strip. The crash bar (the running cart's tool switcher, on the fixed
// 320-wide game canvas): `edit` 1 a Make-it-mine cart, 0 code, -1 a compiled
// cart (no EDIT slot).
void moy_chrome_strip_crash(moy_chrome_list_t *l, int cw, int edit, const char *clock,
                            const char *wifi);
// The band every other bar starts with: `light` is a light band (a window's
// toolbar under a light theme, or a light theme's bar).
void moy_chrome_strip_band(moy_chrome_list_t *l, int cw, int bar_h, bool light);
// The OS-owned right zone. `lay` NULL: the fixed game-canvas cluster; else
// the responsive layout's rects (each x, y, w, h) and its chrome scale.
typedef struct {
    int16_t clock_x, text_dy, cs;
    int16_t wifi[4], batt[4], menu[4], close[4];
} moy_chrome_lay_t;
void moy_chrome_strip_right(moy_chrome_list_t *l, const moy_chrome_lay_t *lay,
                            const char *clock, const char *wifi, bool show_x);
// A tool's title in its lent zone (x, w), `dy` below the fixed text line.
void moy_chrome_strip_title(moy_chrome_list_t *l, const char *title, int zx, int zw, int dy);

// The system menu: `n` rows of `kind` (0 item, 1 header, 2 separator) and
// `label`, the panel at (x, y, w, h), `sel` the selected row, `fs` its text
// scale and `cs` its row scale.
enum { MOY_MENU_ITEM = 0, MOY_MENU_HEADER = 1, MOY_MENU_SEP = 2 };
#define MOY_MENU_ROWS 16
void moy_chrome_menu(moy_chrome_list_t *l, const uint8_t *kind, const char *const *label,
                     int n, int sel, int x, int y, int w, int h, int fs, int cs);
// The ABOUT box over a `cw` x `ch` canvas at font scale `fs`; `ver` "" when unknown.
void moy_chrome_about(moy_chrome_list_t *l, int cw, int ch, int fs, const char *ver);

// -- the raster ------------------------------------------------------------------

// A glyph the raster may draw: twelve 12-bit rows, MSB the leftmost pixel.
int moy_chrome_glyph_put(const char *kind, const uint16_t rows[12]);
// Draw `l` into an RGB565 buffer `w` x `h` through `pal` (64 index -> word);
// `fs` is the canvas's font scale for a GLYPH op that names none. An ICON op
// draws nothing here (the bar's icons are the shell's sheet).
void moy_chrome_raster(const moy_chrome_list_t *l, uint16_t *px, int w, int h,
                       const uint16_t *pal, int fs);

// -- the kernel's state --------------------------------------------------------

void moy_chrome_notice(const char *title, const char *sub, bool ok, uint32_t until_ms);
void moy_chrome_toast_arm(const char *title, const char *glyph, uint32_t until_ms);
// What is up at `now` over a cw x ch canvas (the pill at `held_ms`, 0: none),
// into `l`: the pill, then the banner, then the toast. False: nothing.
bool moy_chrome_overlay(moy_chrome_list_t *l, uint32_t now, int cw, int ch, int lw, int fs,
                        int status_h, uint32_t held_ms, uint32_t hold_ms);

#endif // MOY_CHROME_H

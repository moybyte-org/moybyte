// moy_chrome: one body for each piece of chrome (moy_chrome.h has the
// contract). The geometry is the shell's frozen layout, constant for
// constant; tests/test_moy_chrome.py holds the replay and the raster to each
// other and the shell's goldens hold the replay to its pixels.

#include <stdio.h>
#include <string.h>

#include "moy_chrome.h"
#include "moy_str.h"
#include "moy_gfx_kernels.h"
#include "moy_text.h"
#include "moy_ui.h"
#include "moy.h"

// -- the inks ----------------------------------------------------------------------

static int16_t g_ink[MOY_INKS];
static bool g_light;
static int g_rings = 1;

void moy_chrome_set_inks(const int16_t inks[MOY_INKS], bool bar_light, int dialog_rings) {
    memcpy(g_ink, inks, sizeof(g_ink));
    g_light = bar_light;
    g_rings = dialog_rings;
}

static uint8_t ink(int i) {
    return (uint8_t)(g_ink[i] & 63);
}

// -- the list --------------------------------------------------------------------

void moy_chrome_clear(moy_chrome_list_t *l) {
    l->n = 0;
    l->tn = 0;
    l->full = false;
}

static moy_chrome_op_t *put(moy_chrome_list_t *l, uint8_t op, int c, int x, int y, int w,
                            int h) {
    if (l->n >= MOY_CHROME_OPS) {
        l->full = true;
        return NULL;
    }
    moy_chrome_op_t *o = &l->op[l->n++];
    o->op = op;
    o->c = (uint8_t)(c & 63);
    o->scale = 0;
    o->src = 0;
    o->x = (int16_t)x;
    o->y = (int16_t)y;
    o->w = (int16_t)w;
    o->h = (int16_t)h;
    o->s = o->n = 0;
    return o;
}

static bool str_in(moy_chrome_list_t *l, moy_chrome_op_t *o, const char *s, size_t n) {
    if (l->tn + n > MOY_CHROME_TEXT) {
        l->full = true;
        l->n--;
        return false;
    }
    memcpy(l->text + l->tn, s, n);
    o->s = l->tn;
    o->n = (uint16_t)n;
    l->tn = (uint16_t)(l->tn + n);
    return true;
}

static void rect(moy_chrome_list_t *l, int x, int y, int w, int h, int c) {
    put(l, MOY_CH_RECT, c, x, y, w, h);
}

static void rectb(moy_chrome_list_t *l, int x, int y, int w, int h, int c) {
    put(l, MOY_CH_RECTB, c, x, y, w, h);
}

// `text` cut to its first `max` bytes (a negative max: all of it), from
// `src` (moy_text.h's sources: the raster refuses a LIT).
static void print(moy_chrome_list_t *l, uint8_t src, const char *s, int max, int x, int y,
                  int c, int scale) {
    size_t n = s != NULL ? strlen(s) : 0;
    if (max >= 0 && n > (size_t)max) {
        n = moy_text_fit((const uint8_t *)s, n, (size_t)max);    // never mid-codepoint
    }
    moy_chrome_op_t *o = put(l, MOY_CH_TEXT, c, x, y, 0, 0);
    if (o != NULL) {
        o->scale = (uint8_t)scale;
        o->src = src;
        str_in(l, o, s != NULL ? s : "", n);
    }
}

static void glyph(moy_chrome_list_t *l, const char *kind, int x, int y, int w, int h, int c,
                  int scale) {
    moy_chrome_op_t *o = put(l, MOY_CH_GLYPH, c, x, y, w, h);
    if (o != NULL) {
        o->scale = (uint8_t)scale;
        str_in(l, o, kind, strlen(kind));
    }
}

static void icon(moy_chrome_list_t *l, const char *kind, int x, int y, int scale) {
    moy_chrome_op_t *o = put(l, MOY_CH_ICON, 0, x, y, 0, 0);
    if (o != NULL) {
        o->scale = (uint8_t)scale;
        str_in(l, o, kind, strlen(kind));
    }
}

// -- the pieces ------------------------------------------------------------------

void moy_chrome_pill(moy_chrome_list_t *l, int cw, int ch, uint32_t held_ms, uint32_t hold_ms) {
    (void)ch;
    int w = cw - 8 < 128 ? cw - 8 : 128, h = 16;   // fits a cart-declared small canvas
    int x = (cw - w) / 2, y = 6;
    rect(l, x, y, w, h, ink(MOY_INK_BLACK));
    rectb(l, x, y, w, h, ink(MOY_INK_LIGHT_GREY));
    const char *label = moy_str(MOY_S_CHROME_HOLD_TO_EXIT);
    int ln = (int)strlen(label);
    print(l, MOY_TEXT_TABLE, label, -1, x + (w - ln * 8) / 2, y + 2, ink(MOY_INK_WHITE), 1);
    if (hold_ms == 0u) {
        return;
    }
    uint32_t el = held_ms > hold_ms ? hold_ms : held_ms;
    int fill = (int)((uint64_t)(uint32_t)(w - 4) * el / hold_ms);
    if (fill > 0) {
        rect(l, x + 2, y + h - 4, fill, 2, ink(MOY_INK_YELLOW));
    }
}

#define CODE_LH 10

// The panel's text, word-wrapped into `cols`-byte lines by the one wrap
// (moy_text_wrap's panel mode: a newline ends a line, a word longer than a
// line breaks across lines at a codepoint), the first `max_rows` printed
// CODE_LH apart.
#define PANEL_ROWS 32
static void panel_lines(moy_chrome_list_t *l, uint8_t src, const char *text, int cols,
                        int max_rows, int x, int y, int c) {
    if (cols < 1) {
        cols = 1;
    }
    if (max_rows > PANEL_ROWS) {
        max_rows = PANEL_ROWS;
    }
    moy_text_line_t lines[PANEL_ROWS];
    const uint8_t *s = (const uint8_t *)text;
    int n = moy_text_wrap(MOY_FONT_PETME128, s, strlen(text), cols, MOY_WRAP_PANEL, lines,
                          max_rows);
    for (int row = 0; row < n && row < max_rows; row++) {
        print(l, src, text + lines[row].off, lines[row].len, x, y + row * CODE_LH, c, 1);
    }
}

// -- the panels' words (#143) -------------------------------------------------------

// Each kind's title, with and without the line, and the stopped run's texts,
// in docs/os_voice_v1.md's Spoken register (§5's failure doctrine: the fact,
// then the next move; no "I", no exclamation, none of law 3's words); the
// hint under them is ENGRAVED. A title fits the panel's 35 columns.
// Each say's string ids (native/moy_spine/strings/en.json), -1 for none.
static const struct {
    int16_t title, title_at, text;
} SAY[MOY_SAYS] = {
    [MOY_SAY_CRASH] = { MOY_S_SAY_CRASH, MOY_S_SAY_CRASH_LINE, -1 },
    [MOY_SAY_STUCK] = { MOY_S_SAY_STUCK, MOY_S_SAY_STUCK_LINE, -1 },
    [MOY_SAY_FIT] = { MOY_S_SAY_FIT, -1, -1 },
    [MOY_SAY_NEWER] = { MOY_S_SAY_NEWER, -1, -1 },
    [MOY_SAY_NOLOAD] = { MOY_S_SAY_NOLOAD, -1, MOY_S_SAY_NOLOAD_TEXT },
    [MOY_SAY_CONSOLE] = { MOY_S_SAY_CONSOLE, -1, MOY_S_SAY_CONSOLE_TEXT },
};

// The runaway watch's words (stuck.text) are recognised in a crash's text by
// their head, the text before its first conversion.
static bool stuck_head_in(const char *text) {
    const char *f = moy_str(MOY_S_STUCK_TEXT);
    const char *pc = strchr(f, '%');
    size_t n = pc != NULL ? (size_t)(pc - f) : strlen(f);
    if (n == 0) {
        return false;
    }
    for (const char *p = text; *p; p++) {
        if (strncmp(p, f, n) == 0) {
            return true;
        }
    }
    return false;
}

int moy_chrome_title(char *out, size_t n, int say, int line) {
    if (say < 0 || say >= MOY_SAYS) {
        say = MOY_SAY_CRASH;
    }
    if (line > 0 && SAY[say].title_at >= 0) {
        return moy_str_fmt(out, n, SAY[say].title_at, line);
    }
    return moy_str_copy(out, n, SAY[say].title);
}

int moy_chrome_crash_title(char *out, size_t n, const char *text, int line) {
    bool stuck = text != NULL && stuck_head_in(text);
    return moy_chrome_title(out, n, stuck ? MOY_SAY_STUCK : MOY_SAY_CRASH, line);
}

int moy_chrome_stuck_text(char *out, size_t n, uint32_t budget_ms) {
    return moy_str_fmt(out, n, MOY_S_STUCK_TEXT, (unsigned)((budget_ms + 999u) / 1000u));
}

const char *moy_chrome_say_text(int say) {
    return say >= 0 && say < MOY_SAYS && SAY[say].text >= 0 ? moy_str(SAY[say].text) : "";
}

// `v` bytes as "N.N MB", rounded up (`up`) or down.
static void mb(char *out, size_t n, uint32_t v, bool up) {
    const uint64_t MB = 1024u * 1024u;
    uint64_t tenths = ((uint64_t)v * 10u + (up ? MB - 1u : 0u)) / MB;
    moy_str_fmt(out, n, MOY_S_FIT_MEGABYTES, (unsigned)(tenths / 10u),
                (unsigned)(tenths % 10u));
}

int moy_chrome_fit_text(char *out, size_t n, const char *title, uint32_t total,
                        uint32_t block, uint32_t free_, uint32_t largest) {
    const char *t = title != NULL && *title ? title : moy_str(MOY_S_FIT_THIS_GAME);
    const char *again = moy_str(MOY_S_FIT_RESTART);
    char a[16], b[16];
    if (total == 0u) {
        return moy_str_fmt(out, n, MOY_S_FIT_UNKNOWN, t, again);
    }
    int id = MOY_S_FIT_PIECES;
    uint32_t need = total, have = free_;
    if (total > free_) {
        id = MOY_S_FIT_NEEDS;
    } else if (block > largest) {
        id = MOY_S_FIT_BLOCK;
        need = block;
        have = largest;
    }
    mb(a, sizeof(a), need, true);
    mb(b, sizeof(b), have, false);
    return moy_str_fmt(out, n, id, t, a, b, again);
}

int moy_chrome_newer_text(char *out, size_t n, const char *title, const char *missing) {
    return moy_str_fmt(out, n, MOY_S_NEWER_TEXT,
                       title != NULL && *title ? title : moy_str(MOY_S_FIT_THIS_GAME),
                       missing != NULL ? missing : "");
}

void moy_chrome_panel(moy_chrome_list_t *l, int cw, int ch, bool notice, const char *title,
                      const char *text, bool compiled) {
    // Sized against the surface: a cart-declared small canvas still gets a
    // panel that fits.
    int w = cw - 12 < 292 ? cw - 12 : 292;
    int h = ch - 16 < 132 ? ch - 16 : 132;
    int x = (cw - w) / 2;
    int y = (ch - h) / 2 < 40 ? (ch - h) / 2 : 40;
    // Never red (#143, docs/os_voice_v1.md §5: no alarm): a crash is peach on
    // purple, a notice orange on blue.
    int edge = ink(notice ? MOY_INK_ORANGE : MOY_INK_PEACH);
    rect(l, x, y, w, h, ink(notice ? MOY_INK_DARK_BLUE : MOY_INK_DARK_PURPLE));
    rectb(l, x, y, w, h, edge);
    rect(l, x, y, w, 14, edge);
    uint8_t tsrc = MOY_TEXT_PY;
    if (title == NULL || !*title) {
        title = moy_str(SAY[notice ? MOY_SAY_FIT : MOY_SAY_CRASH].title);
        tsrc = MOY_TEXT_TABLE;
    }
    print(l, tsrc, title, -1, x + 6, y + 4, ink(MOY_INK_BLACK), 1);
    int cols = (w - 16) / 8;
    int max_rows = (h - 30) / CODE_LH;
    uint8_t src = MOY_TEXT_PY;
    if (text == NULL || !*text) {
        text = moy_str(MOY_S_CHROME_NO_TEXT);
        src = MOY_TEXT_TABLE;
    }
    if (max_rows > 0) {
        panel_lines(l, src, text, cols, max_rows, x + 8, y + 20,
                    ink(notice ? MOY_INK_WHITE : MOY_INK_PEACH));
    }
    // A compiled cart's trap has no source line behind it and no EDIT.
    print(l, MOY_TEXT_TABLE, moy_str(compiled ? MOY_S_CHROME_HINT_HOME : MOY_S_CHROME_HINT_CODE),
          -1, x + 8, y + h - 12, ink(MOY_INK_YELLOW), 1);
}

void moy_chrome_toast(moy_chrome_list_t *l, const char *title, const char *g) {
    int x = 36, y = 26, w = 248, h = 38;
    rect(l, x, y, w, h, ink(MOY_INK_DARK_PURPLE));
    rectb(l, x, y, w, h, ink(MOY_INK_YELLOW));
    rect(l, x, y, w, 12, ink(MOY_INK_YELLOW));
    glyph(l, "trophy", x + 2, y - 1, 12, 12, ink(MOY_INK_BLACK), 0);
    print(l, MOY_TEXT_TABLE, moy_str(MOY_S_CHROME_ACHIEVEMENT), -1, x + 16, y + 2,
          ink(MOY_INK_BLACK), 1);
    glyph(l, g != NULL ? g : "", x + 6, y + 16, 16, 16, ink(MOY_INK_YELLOW), 0);
    print(l, MOY_TEXT_PY, title, 24, x + 28, y + 20, ink(MOY_INK_WHITE), 2);
}

void moy_chrome_banner(moy_chrome_list_t *l, int lw, int fs, int status_h, const char *title,
                       const char *sub, bool ok) {
    int accent = ink(ok ? MOY_INK_PLAY : MOY_INK_ORANGE);
    int tn = (int)strlen(title);
    int want = (tn + 2) * 8 * fs * 2;
    if (want < 180 * fs) {
        want = 180 * fs;
    }
    int w = lw - 16 * fs < want ? lw - 16 * fs : want;
    int h = 34 * fs;
    int x = (lw - w) / 2;
    int y = status_h + 6 * fs;
    rect(l, x, y, w, h, ink(MOY_INK_SURFACE));
    rectb(l, x, y, w, h, accent);
    rect(l, x, y, w, 3 * fs, accent);            // a lit edge, not a full title bar
    glyph(l, "gear", x + 5 * fs, y + 8 * fs, 14 * fs, 14 * fs, accent, 0);
    print(l, MOY_TEXT_PY, title, 22, x + 22 * fs, y + 7 * fs, ink(MOY_INK_INK), 2 * fs);
    if (sub != NULL && *sub) {
        print(l, MOY_TEXT_PY, sub, 26, x + 22 * fs, y + 22 * fs, ink(MOY_INK_INK_DIM), fs);
    }
}

// The bar's fixed game-canvas geometry (runtime/bar_layer.py's constants).
#define BAR_H 18
#define BAR_ICON 16
#define BAR_STRIDE 18
#define BAR_Y 1
#define BAR_BATT_X (320 - 2 - BAR_ICON)
#define BAR_WIFI_X (BAR_BATT_X - BAR_STRIDE)
#define BAR_CLOCK_X (BAR_WIFI_X - 2 - 5 * 8)
#define ZONE_GEAR_X (BAR_WIFI_X - BAR_STRIDE)
#define ZONE_X_X (ZONE_GEAR_X - BAR_STRIDE)
#define ZONE_CLOCK_X (ZONE_X_X - 2 - 5 * 8)

void moy_chrome_strip_crash(moy_chrome_list_t *l, int cw, int edit, const char *clock,
                            const char *wifi) {
    rect(l, 0, 0, cw, BAR_H, ink(MOY_INK_BAR));
    rect(l, 0, BAR_H - 1, cw, 1, ink(MOY_INK_BAR_EDGE));         // the shelf edge
    // The tool switcher: the menu glyph leftmost, then HOME, EDIT|CODE (none
    // for a compiled cart), PAINT, MAP, BLOCKS, MUSIC one stride apart.
    glyph(l, "menu", 2, BAR_Y, BAR_ICON, BAR_ICON, ink(MOY_INK_CHROME), 0);
    icon(l, "home", 2 + BAR_STRIDE, BAR_Y, 0);
    if (edit >= 0) {
        icon(l, edit ? "edit" : "code", 2 + 2 * BAR_STRIDE, BAR_Y, 0);
    }
    icon(l, "paint", 2 + 3 * BAR_STRIDE, BAR_Y, 0);
    icon(l, "map", 2 + 4 * BAR_STRIDE, BAR_Y, 0);
    icon(l, "blocks", 2 + 5 * BAR_STRIDE, BAR_Y, 0);
    icon(l, "music", 2 + 6 * BAR_STRIDE, BAR_Y, 0);
    print(l, MOY_TEXT_PY, clock, -1, BAR_CLOCK_X, 3, ink(MOY_INK_CHROME_DIM), 1);
    icon(l, wifi, BAR_WIFI_X, BAR_Y, 0);
    icon(l, "batt", BAR_BATT_X, BAR_Y, 0);
}

void moy_chrome_strip_band(moy_chrome_list_t *l, int cw, int bar_h, bool light) {
    if (light) {
        rect(l, 0, 0, cw, bar_h, ink(MOY_INK_SURFACE));
        rect(l, 0, bar_h - 1, cw, 1, ink(MOY_INK_BORDER));
    } else {
        rect(l, 0, 0, cw, bar_h, ink(MOY_INK_BAR));
        rect(l, 0, bar_h - 1, cw, 1, ink(MOY_INK_BAR_EDGE));
    }
}

void moy_chrome_strip_right(moy_chrome_list_t *l, const moy_chrome_lay_t *lay,
                            const char *clock, const char *wifi, bool show_x) {
    if (lay == NULL) {
        print(l, MOY_TEXT_PY, clock, -1, ZONE_CLOCK_X, 3, ink(MOY_INK_CHROME_DIM), 1);
        icon(l, wifi, BAR_WIFI_X, BAR_Y, 0);
        icon(l, "batt", BAR_BATT_X, BAR_Y, 0);
        glyph(l, "menu", ZONE_GEAR_X, BAR_Y, BAR_ICON, BAR_ICON, ink(MOY_INK_CHROME), 0);
        if (show_x) {
            icon(l, "close", ZONE_X_X, BAR_Y, 0);
        }
        return;
    }
    int cs = lay->cs;
    print(l, MOY_TEXT_PY, clock, -1, lay->clock_x, 3 + lay->text_dy, ink(MOY_INK_CHROME_DIM), 1);
    icon(l, wifi, lay->wifi[0], lay->wifi[1], cs);
    icon(l, "batt", lay->batt[0], lay->batt[1], cs);
    glyph(l, "menu", lay->menu[0], lay->menu[1], lay->menu[2], lay->menu[3],
          ink(MOY_INK_CHROME), cs);
    if (show_x) {
        icon(l, "close", lay->close[0], lay->close[1], cs);
    }
}

void moy_chrome_strip_title(moy_chrome_list_t *l, const char *title, int zx, int zw, int dy) {
    int maxc = zw / 8;                          // 8px cells in the lent rect
    if (maxc > 0) {
        print(l, MOY_TEXT_PY, title, maxc, zx, 3 + dy, ink(MOY_INK_CHROME_DIM), 1);
    }
}

// The system menu's frozen geometry (runtime/console.py's _POPUP_*).
#define POPUP_Y 18
#define POPUP_ROW_H 12
#define POPUP_PAD_X 4
#define POPUP_SEP_H 1

static void dialog(moy_chrome_list_t *l, int x, int y, int w, int h) {
    rect(l, x, y, w, h, ink(MOY_INK_PANEL));
    int c = ink(MOY_INK_EDGE);
    for (int i = 0; i < g_rings; i++) {
        rectb(l, x + i, y + i, w - 2 * i, h - 2 * i, c);
    }
}

void moy_chrome_menu(moy_chrome_list_t *l, const uint8_t *kind, const char *const *label,
                     int n, int sel, int x, int y, int w, int h, int fs, int cs) {
    dialog(l, x, y, w, h);
    int cy = POPUP_Y * cs;
    int row_h = POPUP_ROW_H * cs;
    // Rows sit 1px inside the ring, so the label's inset from the PANEL edge
    // is one less from the row's.
    int pad = POPUP_PAD_X * fs - 1;
    int dy = 2 * fs + (row_h - POPUP_ROW_H * fs) / 2;
    int fw = 8 * fs;
    for (int i = 0; i < n; i++) {
        if (kind[i] == MOY_MENU_SEP) {
            rect(l, x + 1, cy, w - 2, POPUP_SEP_H * cs, ink(MOY_INK_EDGE));
            cy += POPUP_SEP_H * cs;
            continue;
        }
        bool header = kind[i] == MOY_MENU_HEADER;
        bool on = !header && i == sel;
        int field = header ? -1 : g_ink[on ? MOY_INK_MENU_FIELD_ON : MOY_INK_MENU_FIELD_OFF];
        int c = header ? (g_light ? ink(MOY_INK_INK_DIM) : ink(MOY_INK_DARK_GREY))
                       : ink(on ? MOY_INK_MENU_INK_ON : MOY_INK_MENU_INK_OFF);
        int rx = x + 1, rw = w - 2;
        if (field >= 0) {
            rect(l, rx, cy, rw, row_h, field);
        }
        int tx = rx + pad;
        int right = rx + rw - pad;
        int maxc = (right - tx) / fw;
        if (maxc < 0) {
            maxc = 0;
        }
        if (label[i] != NULL && label[i][0] && maxc > 0) {
            print(l, MOY_TEXT_PY, label[i], maxc, tx, cy + dy, c, 1);
        }
        cy += row_h;
    }
}

void moy_chrome_about(moy_chrome_list_t *l, int cw, int ch, int fs, const char *ver) {
    const char *lines[4] = {moy_str(MOY_S_ABOUT_NAME),
                            ver != NULL && *ver ? ver : moy_str(MOY_S_ABOUT_VERSION_UNKNOWN), "",
                            moy_str(MOY_S_ABOUT_CLOSE)};
    // The version is the caller's; the rest is the table's.
    const uint8_t src[4] = {MOY_TEXT_TABLE, ver != NULL && *ver ? MOY_TEXT_PY : MOY_TEXT_TABLE,
                            MOY_TEXT_TABLE, MOY_TEXT_TABLE};
    int fw = 8 * fs;
    int w = 0;
    for (int i = 0; i < 4; i++) {
        int lw = (int)strlen(lines[i]) * fw;
        if (lw > w) {
            w = lw;
        }
    }
    w += 24 * fs;
    if (w > cw - 16 * fs) {
        w = cw - 16 * fs;
    }
    int h = 20 * fs + 4 * 12 * fs;
    int x = (cw - w) / 2, y = (ch - h) / 2;
    dialog(l, x, y, w, h);
    int c = g_light ? ink(MOY_INK_INK) : ink(MOY_INK_CHROME);
    int ly = y + 10 * fs;
    for (int i = 0; i < 4; i++) {
        int lw = (int)strlen(lines[i]) * fw;
        print(l, src[i], lines[i], -1, x + (w - lw) / 2, ly, c, 1);
        ly += 12 * fs;
    }
}

// -- the raster --------------------------------------------------------------------

void moy_chrome_raster(const moy_chrome_list_t *l, uint16_t *px, int w, int h,
                       const uint16_t *pal, int fs) {
    size_t cap = (size_t)w * (size_t)h;
    if (fs < 1) {
        fs = 1;
    }
    for (uint16_t i = 0; i < l->n; i++) {
        const moy_chrome_op_t *o = &l->op[i];
        int col = pal[o->c & 63];
        switch (o->op) {
            case MOY_CH_RECT:
                mg_fill_rect(px, cap, w, o->x, o->y, o->w, o->h, col);
                break;
            case MOY_CH_RECTB:
                // The same four clipped fills the canvas's rectb issues.
                mg_fill_rect(px, cap, w, o->x, o->y, o->w, 1, col);
                mg_fill_rect(px, cap, w, o->x, o->y + o->h - 1, o->w, 1, col);
                mg_fill_rect(px, cap, w, o->x, o->y, 1, o->h, col);
                mg_fill_rect(px, cap, w, o->x + o->w - 1, o->y, 1, o->h, col);
                break;
            case MOY_CH_TEXT: {
                // The canvas's own font scale: a print's scale argument is
                // ignored on every tier (SPEC.md 6). The one text path, which
                // refuses a LIT op.
                moy_text_target_t t = {px, cap, w, 0, 0, 0, 0, w, h};
                moy_text(&t, MOY_FONT_PETME128, o->src, (const uint8_t *)l->text + o->s, o->n,
                         o->x, o->y, (uint16_t)col, fs, MOY_TEXT_LEFT, -1);
                break;
            }
            case MOY_CH_GLYPH: {
                // The one glyph raster (native/moy_ui); an unknown glyph draws
                // nothing.
                moy_text_target_t t = {px, cap, w, 0, 0, 0, 0, w, h};
                moy_ui_glyph_draw(&t, moy_ui_glyph_id(l->text + o->s, o->n), o->x, o->y, o->w,
                                  o->h, (uint16_t)col, o->scale ? o->scale : fs);
                break;
            }
            default:
                break;                      // ICON: the shell's sheet
        }
    }
}

// -- the kernel's state ------------------------------------------------------------

static struct {
    char title[48], sub[48];
    bool ok;
    uint32_t until;
    bool up;
    char toast[48], glyph[12];
    uint32_t toast_until;
    bool toast_up;
} K;

void moy_chrome_notice(const char *title, const char *sub, bool ok, uint32_t until_ms) {
    snprintf(K.title, sizeof(K.title), "%s", title ? title : "");
    snprintf(K.sub, sizeof(K.sub), "%s", sub ? sub : "");
    K.ok = ok;
    K.until = until_ms;
    K.up = true;
}

void moy_chrome_toast_arm(const char *title, const char *g, uint32_t until_ms) {
    snprintf(K.toast, sizeof(K.toast), "%s", title ? title : "");
    snprintf(K.glyph, sizeof(K.glyph), "%s", g ? g : "");
    K.toast_until = until_ms;
    K.toast_up = true;
}

bool moy_chrome_overlay(moy_chrome_list_t *l, uint32_t now, int cw, int ch, int lw, int fs,
                        int status_h, uint32_t held_ms, uint32_t hold_ms) {
    moy_chrome_clear(l);
    if (held_ms) {
        moy_chrome_pill(l, cw, ch, held_ms, hold_ms);
    }
    if (K.up && (int32_t)(K.until - now) <= 0) {
        K.up = false;
    }
    if (K.up) {
        moy_chrome_banner(l, lw, fs, status_h, K.title, K.sub, K.ok);
    }
    if (K.toast_up && (int32_t)(K.toast_until - now) <= 0) {
        K.toast_up = false;
    }
    if (K.toast_up) {
        moy_chrome_toast(l, K.toast, K.glyph);
    }
    return l->n > 0;
}

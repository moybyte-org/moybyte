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
#include "moy_canvas.h"
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
    return (uint8_t)(g_ink[i] & 127);
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
    o->c = (uint8_t)(c & 127);
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

int moy_chrome_newer_text(char *out, size_t n, const char *missing) {
    return moy_str_fmt(out, n, MOY_S_NEWER_TEXT, missing != NULL ? missing : "");
}

void moy_chrome_panel(moy_chrome_list_t *l, int cw, int ch, bool notice, const char *title,
                      const char *text) {
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
    print(l, MOY_TEXT_TABLE, moy_str(MOY_S_CHROME_HINT_HOME), -1, x + 8, y + h - 12,
          ink(MOY_INK_YELLOW), 1);
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

void moy_chrome_strip_cell(moy_chrome_list_t *l, const char *kind, int x, int y, int w, int h,
                           int cs, int dy, const char *s, bool across) {
    if (strcmp(kind, "menu") == 0) {
        glyph(l, "menu", x, y, w, h, ink(MOY_INK_CHROME), cs);
    } else if (strcmp(kind, "close") == 0) {
        icon(l, "close", x, y, cs);
    } else if (strcmp(kind, "wifi") == 0) {
        icon(l, s, x, y, cs);
    } else if (strcmp(kind, "battery") == 0) {
        icon(l, "batt", x, y, cs);
    } else if (strcmp(kind, "clock") == 0 || strcmp(kind, "title") == 0
               || strcmp(kind, "text") == 0) {
        // One line on its bar's text row; a title cut to the cell's 8px
        // columns; on a side bar the clock's hours over its minutes. The
        // title and the clock are the bar's bright ink, a text the dim.
        const char *t = s != NULL ? s : "";
        bool title = strcmp(kind, "title") == 0, clock = strcmp(kind, "clock") == 0;
        int lines = 1, max = -1, ty = across ? y : 3 + dy;
        if (title) {
            max = w / 8;
            if (max <= 0) {
                return;
            }
        } else if (clock && across) {
            if (strlen(t) < 5) {
                return;
            }
            lines = 2;
            max = 2;
        }
        for (int i = 0; i < lines; i++) {
            print(l, MOY_TEXT_PY, t + 3 * i, max, x, ty + i * (h / 2),
                  ink(title || clock ? MOY_INK_CHROME : MOY_INK_CHROME_DIM), 1);
        }
    } else if (strcmp(kind, "separator") == 0) {
        if (across) {
            rect(l, x + 2 * cs, y + h / 2, w - 4 * cs, 1, ink(MOY_INK_CHROME_DIM));
        } else {
            rect(l, x + w / 2, y, 1, h, ink(MOY_INK_CHROME_DIM));
        }
    }
}

// The system menu's frozen geometry (runtime/widgets.py's _POPUP_*).
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
    int cy = y;                     // the rows start at the panel's top edge
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
    char head[48], name[48], icon[16];
    bool secret;
    uint32_t toast_at, toast_until;
    bool toast_up;
} K;

void moy_chrome_notice(const char *title, const char *sub, bool ok, uint32_t until_ms) {
    snprintf(K.title, sizeof(K.title), "%s", title ? title : "");
    snprintf(K.sub, sizeof(K.sub), "%s", sub ? sub : "");
    K.ok = ok;
    K.until = until_ms;
    K.up = true;
}

void moy_chrome_toast_arm(const char *head, const char *name, const char *icon, bool secret,
                          uint32_t until_ms) {
    snprintf(K.head, sizeof(K.head), "%s", head ? head : "");
    snprintf(K.name, sizeof(K.name), "%s", name ? name : "");
    snprintf(K.icon, sizeof(K.icon), "%s", icon ? icon : "");
    K.secret = secret;
    K.toast_until = until_ms;
    K.toast_at = until_ms - MOY_CHROME_TOAST_MS;
    K.toast_up = true;
}

static moy_ui_span_t span(const char *s) {
    return (moy_ui_span_t){(const uint8_t *)s, (uint16_t)strlen(s)};
}

bool moy_chrome_overlay_draw(uint16_t *px, int w, int h, const uint16_t *pal, uint32_t now) {
    if (K.up && (int32_t)(K.until - now) <= 0) {
        K.up = false;
    }
    if (K.toast_up && (int32_t)(K.toast_until - now) <= 0) {
        K.toast_up = false;
    }
    if (!K.up && !K.toast_up) {
        return false;
    }
    // the run's canvas lent as a row of the shell's look: its palette below,
    // the theme's block above, the theme's faces (a cart's boundary put aside
    // for the banner, which is the system's)
    moy_canvas_row_t row;
    memset(&row, 0, sizeof(row));
    row.px = px;
    row.cap = (uint32_t)(w * h);
    row.w = (uint16_t)w;
    row.h = (uint16_t)h;
    row.st[MOY_ST_CX1] = row.st[MOY_ST_W] = w;
    row.st[MOY_ST_CY1] = row.st[MOY_ST_H] = h;
    row.st[MOY_ST_FONT_SCALE] = 1;
    memcpy(row.pal, pal, MOY_CANVAS_BLOCK * sizeof(uint16_t));
    memcpy(&row.pal[MOY_CANVAS_BLOCK], moy_canvas_block_words(),
           MOY_CANVAS_BLOCK * sizeof(uint16_t));
    moy_ui_sink_t s = {MOY_UI_SINK_ROW, 1, 0, &row};
    int was = moy_ui_cart_text(0);
    if (K.up) {
        static const char ok[] = "check", warn[] = "warn";
        const char *ic = K.ok ? ok : warn;
        size_t n = strlen(ic);
        moy_ui_unlock(&s, w / 2, 6, span(K.title), span(K.sub),
                      moy_ui_icon_slot((const uint8_t *)ic, n),
                      moy_ui_icon_glyph((const uint8_t *)ic, n), 0, 1, 0, NULL);
    }
    if (K.toast_up) {
        size_t n = strlen(K.icon);
        uint32_t el = now - K.toast_at;
        int burst = !K.secret ? 0 : el >= MOY_CHROME_BURST_MS ? 255
                  : 1 + (int)(el * 254u / MOY_CHROME_BURST_MS);
        moy_ui_unlock(&s, w / 2, 6, span(K.head), span(K.name),
                      moy_ui_icon_slot((const uint8_t *)K.icon, n),
                      moy_ui_icon_glyph((const uint8_t *)K.icon, n), burst, 1, 0, NULL);
    }
    moy_ui_cart_text(was);
    return true;
}

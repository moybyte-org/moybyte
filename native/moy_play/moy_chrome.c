// moy_chrome: one body for each piece of chrome (moy_chrome.h has the
// contract). The geometry is the shell's frozen layout, constant for
// constant; tests/test_moy_chrome.py holds the replay and the raster to each
// other and the shell's goldens hold the replay to its pixels.

#include <stdio.h>
#include <string.h>

#include "moy_chrome.h"
#include "moy_gfx_kernels.h"
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
    o->pad = 0;
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

// `text` cut to its first `max` bytes (a negative max: all of it).
static void print(moy_chrome_list_t *l, const char *s, int max, int x, int y, int c,
                  int scale) {
    size_t n = s != NULL ? strlen(s) : 0;
    if (max >= 0 && n > (size_t)max) {
        n = (size_t)max;
    }
    moy_chrome_op_t *o = put(l, MOY_CH_TEXT, c, x, y, 0, 0);
    if (o != NULL) {
        o->scale = (uint8_t)scale;
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
    static const char LABEL[] = "HOLD TO EXIT";
    int ln = (int)sizeof(LABEL) - 1;
    print(l, LABEL, -1, x + (w - ln * 8) / 2, y + 2, ink(MOY_INK_WHITE), 1);
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

// The panel's text, word-wrapped into `cols`-byte lines, a word longer than
// that hard-split (runtime's _wrap, line for line), the first `max_rows`
// printed CODE_LH apart.
static void panel_lines(moy_chrome_list_t *l, const char *text, int cols, int max_rows, int x,
                        int y, int c) {
    if (cols < 1) {
        cols = 1;
    }
    int row = 0;
    char line[256];
    const char *t = text;
    for (;;) {
        const char *pe = strchr(t, '\n');
        if (pe == NULL) {
            pe = t + strlen(t);
        }
        size_t ln = 0;                  // the line being built
        const char *w = t;
        for (;;) {
            const char *we = w;
            while (we < pe && *we != ' ') {
                we++;
            }
            size_t wn = (size_t)(we - w);
            while (wn > (size_t)cols) {
                if (ln) {
                    if (row < max_rows) {
                        print(l, line, (int)ln, x, y + row * CODE_LH, c, 1);
                    }
                    row++;
                    ln = 0;
                }
                if (row < max_rows) {
                    print(l, w, cols, x, y + row * CODE_LH, c, 1);
                }
                row++;
                w += cols;
                wn -= (size_t)cols;
            }
            if (ln == 0) {
                memcpy(line, w, wn);
                ln = wn;
            } else if (ln + 1u + wn <= (size_t)cols) {
                line[ln] = ' ';
                memcpy(line + ln + 1, w, wn);
                ln += 1u + wn;
            } else {
                if (row < max_rows) {
                    print(l, line, (int)ln, x, y + row * CODE_LH, c, 1);
                }
                row++;
                memcpy(line, w, wn);
                ln = wn;
            }
            if (we >= pe) {
                break;
            }
            w = we + 1;
        }
        if (row < max_rows) {
            print(l, line, (int)ln, x, y + row * CODE_LH, c, 1);
        }
        row++;
        if (*pe == 0 || row >= max_rows) {
            return;
        }
        t = pe + 1;
    }
}

// -- the panels' words (#143) -------------------------------------------------------

// Each kind's title, with and without the line, and the stopped run's texts,
// in docs/os_voice_v1.md's Spoken register (§5's failure doctrine: the fact,
// then the next move; no "I", no exclamation, none of law 3's words); the
// hint under them is ENGRAVED. A title fits the panel's 35 columns.
static const struct {
    const char *title, *title_at, *text;
} SAY[MOY_SAYS] = {
    [MOY_SAY_CRASH] = { "Your game stopped.", "Your game stopped on line %d.", NULL },
    [MOY_SAY_STUCK] = { "Your game got stuck.", "Your game got stuck on line %d.", NULL },
    [MOY_SAY_FIT] = { "Too big for this console.", NULL, NULL },
    [MOY_SAY_NEWER] = { "Needs a newer console.", NULL, NULL },
    [MOY_SAY_NOLOAD] = { "This cart didn't open.", NULL,
                         "Its files didn't read back right. Get it again from Get "
                         "Carts." },
    [MOY_SAY_CONSOLE] = { "This game needs the console.", NULL,
                          "Open it again from home to play it." },
};
// The runaway watch's words, the head a stuck title is recognised by first.
static const char STUCK_HEAD[] = "stuck: one frame ran over ";
static const char STUCK_TEXT[] = "stuck: one frame ran over %u s. Look for a loop that "
                                 "never ends.";
// The way out, under the text.
static const char HINT_CODE[] = "TAP CODE TO SEE WHY";
static const char HINT_HOME[] = "TAP HOME TO LEAVE";
// The next move a fit notice offers: a fresh start has the most memory in
// one piece.
static const char FIT_RESTART[] = " Restarting the console might free enough.";

int moy_chrome_title(char *out, size_t n, int say, int line) {
    if (say < 0 || say >= MOY_SAYS) {
        say = MOY_SAY_CRASH;
    }
    if (line > 0 && SAY[say].title_at != NULL) {
        return snprintf(out, n, SAY[say].title_at, line);
    }
    return snprintf(out, n, "%s", SAY[say].title);
}

int moy_chrome_crash_title(char *out, size_t n, const char *text, int line) {
    bool stuck = text != NULL && strstr(text, STUCK_HEAD) != NULL;
    return moy_chrome_title(out, n, stuck ? MOY_SAY_STUCK : MOY_SAY_CRASH, line);
}

int moy_chrome_stuck_text(char *out, size_t n, uint32_t budget_ms) {
    return snprintf(out, n, STUCK_TEXT, (unsigned)((budget_ms + 999u) / 1000u));
}

const char *moy_chrome_say_text(int say) {
    return say >= 0 && say < MOY_SAYS && SAY[say].text != NULL ? SAY[say].text : "";
}

// `v` bytes as "N.N MB", rounded up (`up`) or down.
static void mb(char *out, size_t n, uint32_t v, bool up) {
    const uint64_t MB = 1024u * 1024u;
    uint64_t tenths = ((uint64_t)v * 10u + (up ? MB - 1u : 0u)) / MB;
    snprintf(out, n, "%u.%u MB", (unsigned)(tenths / 10u), (unsigned)(tenths % 10u));
}

int moy_chrome_fit_text(char *out, size_t n, const char *title, uint32_t total,
                        uint32_t block, uint32_t free_, uint32_t largest) {
    const char *t = title != NULL && *title ? title : "This game";
    char a[16], b[16];
    if (total == 0u) {
        return snprintf(out, n, "%s needs more memory than this console has free.%s", t,
                        FIT_RESTART);
    }
    if (total > free_) {
        mb(a, sizeof(a), total, true);
        mb(b, sizeof(b), free_, false);
        return snprintf(out, n, "%s needs %s of memory to run. This console has %s free.%s",
                        t, a, b, FIT_RESTART);
    }
    if (block > largest) {
        mb(a, sizeof(a), block, true);
        mb(b, sizeof(b), largest, false);
        return snprintf(out, n, "%s needs %s of memory in one piece. The biggest piece this "
                        "console has free is %s.%s", t, a, b, FIT_RESTART);
    }
    mb(a, sizeof(a), total, true);
    mb(b, sizeof(b), free_, false);
    return snprintf(out, n, "%s needs %s of memory to run. This console has %s free, but "
                    "not in pieces it can use.%s", t, a, b, FIT_RESTART);
}

int moy_chrome_newer_text(char *out, size_t n, const char *title, const char *missing) {
    return snprintf(out, n, "%s needs a newer console (missing: %s). Update this console "
                    "in Settings, then try again.", title != NULL && *title ? title : "This game",
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
    char head[48];
    if (title == NULL || !*title) {
        moy_chrome_title(head, sizeof(head), notice ? MOY_SAY_FIT : MOY_SAY_CRASH, 0);
        title = head;
    }
    print(l, title, -1, x + 6, y + 4, ink(MOY_INK_BLACK), 1);
    int cols = (w - 16) / 8;
    int max_rows = (h - 30) / CODE_LH;
    if (text == NULL || !*text) {
        text = "It stopped without saying why.";
    }
    if (max_rows > 0) {
        panel_lines(l, text, cols, max_rows, x + 8, y + 20,
                    ink(notice ? MOY_INK_WHITE : MOY_INK_PEACH));
    }
    // A compiled cart's trap has no source line behind it and no EDIT.
    print(l, compiled ? HINT_HOME : HINT_CODE, -1, x + 8, y + h - 12, ink(MOY_INK_YELLOW), 1);
}

void moy_chrome_toast(moy_chrome_list_t *l, const char *title, const char *g) {
    int x = 36, y = 26, w = 248, h = 38;
    rect(l, x, y, w, h, ink(MOY_INK_DARK_PURPLE));
    rectb(l, x, y, w, h, ink(MOY_INK_YELLOW));
    rect(l, x, y, w, 12, ink(MOY_INK_YELLOW));
    glyph(l, "trophy", x + 2, y - 1, 12, 12, ink(MOY_INK_BLACK), 0);
    print(l, "ACHIEVEMENT UNLOCKED!", -1, x + 16, y + 2, ink(MOY_INK_BLACK), 1);
    glyph(l, g != NULL ? g : "", x + 6, y + 16, 16, 16, ink(MOY_INK_YELLOW), 0);
    print(l, title, 24, x + 28, y + 20, ink(MOY_INK_WHITE), 2);
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
    print(l, title, 22, x + 22 * fs, y + 7 * fs, ink(MOY_INK_INK), 2 * fs);
    if (sub != NULL && *sub) {
        print(l, sub, 26, x + 22 * fs, y + 22 * fs, ink(MOY_INK_INK_DIM), fs);
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
    print(l, clock, -1, BAR_CLOCK_X, 3, ink(MOY_INK_CHROME_DIM), 1);
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
        print(l, clock, -1, ZONE_CLOCK_X, 3, ink(MOY_INK_CHROME_DIM), 1);
        icon(l, wifi, BAR_WIFI_X, BAR_Y, 0);
        icon(l, "batt", BAR_BATT_X, BAR_Y, 0);
        glyph(l, "menu", ZONE_GEAR_X, BAR_Y, BAR_ICON, BAR_ICON, ink(MOY_INK_CHROME), 0);
        if (show_x) {
            icon(l, "close", ZONE_X_X, BAR_Y, 0);
        }
        return;
    }
    int cs = lay->cs;
    print(l, clock, -1, lay->clock_x, 3 + lay->text_dy, ink(MOY_INK_CHROME_DIM), 1);
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
        print(l, title, maxc, zx, 3 + dy, ink(MOY_INK_CHROME_DIM), 1);
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
            print(l, label[i], maxc, tx, cy + dy, c, 1);
        }
        cy += row_h;
    }
}

void moy_chrome_about(moy_chrome_list_t *l, int cw, int ch, int fs, const char *ver) {
    const char *lines[4] = {"moybyte", ver != NULL && *ver ? ver : "v0.4", "", "TAP TO CLOSE"};
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
        print(l, lines[i], -1, x + (w - lw) / 2, ly, c, 1);
        ly += 12 * fs;
    }
}

// -- the raster --------------------------------------------------------------------

#define GLYPHS 32

static struct {
    char kind[12];
    uint16_t rows[12];
} g_glyph[GLYPHS];

int moy_chrome_glyph_put(const char *kind, const uint16_t rows[12]) {
    int at = -1;
    for (int i = 0; i < GLYPHS; i++) {
        if (strncmp(g_glyph[i].kind, kind, sizeof(g_glyph[i].kind)) == 0) {
            at = i;
            break;
        }
        if (at < 0 && g_glyph[i].kind[0] == 0) {
            at = i;
        }
    }
    if (at < 0 || strlen(kind) >= sizeof(g_glyph[0].kind)) {
        return -1;
    }
    snprintf(g_glyph[at].kind, sizeof(g_glyph[at].kind), "%s", kind);
    memcpy(g_glyph[at].rows, rows, sizeof(g_glyph[at].rows));
    return 0;
}

static const uint16_t *glyph_rows(const char *kind, size_t n) {
    for (int i = 0; i < GLYPHS; i++) {
        if (g_glyph[i].kind[0] && strlen(g_glyph[i].kind) == n
                && memcmp(g_glyph[i].kind, kind, n) == 0) {
            return g_glyph[i].rows;
        }
    }
    return NULL;
}

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
            case MOY_CH_TEXT:
                // The canvas's own font scale: a print's scale argument is
                // ignored on every tier (SPEC.md 6).
                mg_text(px, cap, w, (const uint8_t *)l->text + o->s, o->n, o->x, o->y, col,
                        moy_font_data, 96, 32, fs, 0, 0, 0, 0, w, h);
                break;
            case MOY_CH_GLYPH: {
                const uint16_t *rows = glyph_rows(l->text + o->s, o->n);
                if (rows == NULL) {
                    break;                  // an unknown glyph draws nothing
                }
                int sc = o->scale ? o->scale : fs;
                int span = 12 * sc;
                int ox = o->x + (o->w - span) / 2, oy = o->y + (o->h - span) / 2;
                for (int r = 0; r < 12; r++) {
                    int run = 0;
                    for (int c = 0; c <= 12; c++) {
                        bool on = c < 12 && (rows[r] & (1u << (11 - c)));
                        if (on) {
                            run++;
                        } else if (run) {
                            mg_fill_rect(px, cap, w, ox + (c - run) * sc, oy + r * sc,
                                         run * sc, sc, col);
                            run = 0;
                        }
                    }
                }
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

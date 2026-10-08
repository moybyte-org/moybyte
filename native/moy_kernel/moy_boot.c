// moy_boot: the boot logo's raster. The header has the design.

#include "moy_boot.h"

// libmoy's font: 96 glyphs from 0x20, one byte per COLUMN, bit k row k.
extern const uint8_t moy_font_data[96 * 8];

// MOY64's RGB565 (runtime/palette.py, device_canvas.PAL565), the entries the
// logo uses: the field (dark_blue), the wordmark (white, indigo) and Moy's
// skin.
#define C_FIELD  0x194Au
#define C_WHITE  0xFF9Du
#define C_INDIGO 0x83B3u

// chrome.py's "moy" art: '.' is clear, a hex digit a palette index.
static const char *const MOY_ART[16] = {
    "................", "...0000000......", "..0ddddddd0.....", ".0d66ddddd0.....",
    ".0dddddddd0.....", ".0dddddddd0000..", ".0dd77d77ddddd0.", ".0dd70d70ddddd0.",
    ".0dddddddddddd0.", ".0dd0ddd0ddddd0.", ".0ddd000dddddd0.", ".0dddddddddddd0.",
    ".0dddddddddddd0.", "..022222222220..", "..02220002220...", "...000...000....",
};

static int art_colour(char ch, uint16_t *out) {
    switch (ch) {
        case '0': *out = 0x0000u; return 1;
        case '2': *out = 0x792Au; return 1;      // dark_purple
        case '6': *out = 0xC618u; return 1;      // light_grey
        case '7': *out = C_WHITE; return 1;
        case 'd': *out = C_INDIGO; return 1;
        default: return 0;
    }
}

static void word(uint16_t *fb, const moy_rgeom_t *g, int x, int y, const char *s, uint16_t c) {
    for (int n = 0; s[n] != '\0'; n++) {
        const uint8_t *gl = moy_font_data + ((unsigned char)s[n] - 0x20) * 8;
        for (int col = 0; col < 8; col++) {
            for (int row = 0, bits = gl[col]; bits; row++, bits >>= 1) {
                if (bits & 1) {
                    moy_recovery_fill(fb, g, x + n * 8 + col, y + row, 1, 1, c);
                }
            }
        }
    }
}

void moy_boot_logo_render(uint16_t *fb, const moy_rgeom_t *g) {
    int w = g->w, h = g->h;
    moy_recovery_fill(fb, g, 0, 0, w, h, C_FIELD);
    int scale = (w < h ? w : h) / 56;
    if (scale < 3) {
        scale = 3;
    }
    int side = 16 * scale;
    int cell = 8, gap = 10;
    int top = (h - (side + gap + cell)) / 2;
    int left = (w - side) / 2;
    for (int y = 0; y < 16; y++) {
        for (int x = 0; x < 16; x++) {
            uint16_t c;
            if (art_colour(MOY_ART[y][x], &c)) {
                moy_recovery_fill(fb, g, left + x * scale, top + y * scale, scale, scale, c);
            }
        }
    }
    int wy = top + side + gap;
    int wx = (w - 7 * cell) / 2;
    word(fb, g, wx, wy, "moy", C_WHITE);
    word(fb, g, wx + 3 * cell, wy, "byte", C_INDIGO);
}

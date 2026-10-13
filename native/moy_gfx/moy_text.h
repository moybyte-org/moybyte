// moy_text: the one UI text path (docs/kernel_toolkit_2026-10.md §4).
//
// Every UI string reaches the raster through here: the system canvases'
// print (the print gate and the `text` kernel at every scale above 1), the
// scaled display type, the toolkit's widths and word wrap, moy_chrome's
// panels and a compiled app's labels. A cart's own `print` stays libmoy's
// `moy_print` at 8 px on every cart runtime (SPEC.md §6) and does not come
// here.
//
// TEXT IS UTF-8 BYTES, MEASURED IN BYTES: the font draws one glyph per byte,
// so a width is the bytes' advances (petme128's are all one 8px cell), a wrap
// counts bytes, and a truncation NEVER cuts a UTF-8 sequence -- it backs up to
// the last whole codepoint. For ASCII a byte is a character; anything else
// draws as the font's first glyph, a glyph per byte, until a font covers it.
//
// EVERY CALL CARRIES ITS SOURCE: TABLE (a string id's text), PY (text a
// runtime passed in), DATA (a number or a user's text formatted into a table
// entry) or LIT (a literal in C). LIT never reaches the raster: moy_text
// refuses it.
//
// THE FONT is a handle-shaped uint32: a row behind every call's font argument.
// Row 0 is petme128, libmoy's compiled-in copy, byte-identical to
// runtime/font.py's (SPEC.md §6 requires it, and the spec's text scenes hold
// it). A theme font is another row: moy_font_load checks a font file
// (docs/theming_2026-09.md §4.7 has the format) and copies it into a buffer
// the row owns, and its id names the slot and the slot's generation, so an id
// outlives its font only as a refusal.

#ifndef MOY_TEXT_H
#define MOY_TEXT_H

#include <stddef.h>
#include <stdint.h>

enum { MOY_TEXT_TABLE = 0, MOY_TEXT_PY = 1, MOY_TEXT_DATA = 2, MOY_TEXT_LIT = 3 };
enum { MOY_TEXT_LEFT = 0, MOY_TEXT_CENTER = 1, MOY_TEXT_RIGHT = 2 };

#define MOY_FONT_PETME128 0u
#define MOY_TEXT_CELL 8         // petme128's advance and height, in pixels
#define MOY_TEXT_ASCENT 7       // petme128's baseline: its capitals' rows

// The font file (docs/theming_2026-09.md §4.7): a 16-byte header, the
// advance table padded to 4 bytes, the glyphs. Every field is a byte but
// `size`, which the reader assembles from its four bytes.
#define MOY_FONT_MAGIC "MFNT"
#define MOY_FONT_VERSION 1
#define MOY_FONT_HEADER 16
#define MOY_FONT_CELL_MAX 16    // a glyph box's width and height, in pixels
#define MOY_FONT_LINE_MAX 32
#define MOY_FONT_BYTES 8192     // the largest file the reader takes
#define MOY_FONT_SLOTS 4        // loaded fonts at once, beside petme128

// What moy_font_load answers: OK, or why the file was refused.
enum {
    MOY_FONT_OK = 0,
    MOY_FONT_BIG = 1,           // over MOY_FONT_BYTES
    MOY_FONT_SHORT = 2,         // under its header, or not the length its header says
    MOY_FONT_MAGIC_BAD = 3,     // not a font file
    MOY_FONT_NEWER = 4,         // a version this reader does not take
    MOY_FONT_BAD = 5,           // a field out of its range, a reserved byte or a padding byte set, a bit below the glyph box
    MOY_FONT_FULL = 6,          // every slot holds a font
    MOY_FONT_NOMEM = 7,
};

// A font's metrics: its glyph box, its line advance, its baseline (rows from
// the box's top) and the bytes its glyphs cover.
typedef struct {
    int w, h, line, ascent, first, count;
} moy_font_info_t;

// Check font file `src` (n bytes, at any address) and load it into a row of
// its own. MOY_FONT_OK with *id set, or a refusal with nothing loaded.
int moy_font_load(const uint8_t *src, size_t n, uint32_t *id);

// Free a loaded font's row; its id is refused from then on. 1, or 0 for an id
// that names no loaded font (petme128 is never freed).
int moy_font_free(uint32_t font);

// Font `font`'s metrics into *out: 1, or 0 for an unknown font.
int moy_font_info(uint32_t font, moy_font_info_t *out);

// Where text lands: an RGB565 buffer `dw` pixels wide holding `cap` pixels,
// the camera and the clip (buffer space, exclusive right and bottom).
typedef struct {
    uint16_t *px;
    size_t cap;
    int dw;
    int cam_x, cam_y;
    int cx0, cy0, cx1, cy1;
} moy_text_target_t;

// Fill a rect into a target: camera-offset, clipped to the clip and the
// buffer, as the canvas's rect and the draw gates fill (the toolkit's
// primitives draw their fields and glyphs with it).
void moy_target_fill(const moy_text_target_t *t, int x, int y, int w, int h, uint16_t col);

// Draw `s` (n bytes) at (x, y) in `col` at `scale`. With maxw >= 0 the text
// fits the box: it truncates to what fits in maxw pixels, at a codepoint
// boundary, and `align` places it in the box. The drawn width, or -1 when
// refused (a LIT source, an unknown font).
int moy_text(const moy_text_target_t *t, uint32_t font, uint8_t source,
             const uint8_t *s, size_t n, int x, int y, uint16_t col, int scale,
             int align, int maxw);

// The width `s` draws at `scale`, in pixels (0 for an unknown font): the sum
// of its glyphs' advances.
int moy_text_width(uint32_t font, const uint8_t *s, size_t n, int scale);

// The longest prefix of `s` that is at most `cells` bytes and ends on a
// codepoint boundary.
size_t moy_text_fit(const uint8_t *s, size_t n, size_t cells);

// The longest prefix of `s` that draws in at most `maxw` pixels at `scale`
// in `font` and ends on a codepoint boundary; 0 for an unknown font or a
// negative width.
size_t moy_text_fit_px(uint32_t font, const uint8_t *s, size_t n, int maxw, int scale);

// Word wrap into lines of at most `cols` bytes, for any known font.
//
// MOY_WRAP_WORDS (the toolkit's): words split on whitespace; a line is its
// words joined by single spaces; a word longer than a line gets a line of
// its own, truncated. A line is the source span from its first word to its
// last, flagged `collapse` when whitespace other than single spaces lies
// inside it (the consumer joins its words with single spaces).
//
// MOY_WRAP_PANEL (moy_chrome's panels): a newline ends a line (an empty
// paragraph is an empty line); words split on spaces, kept as they are; a
// word longer than a line is broken across lines. Every line is the exact
// source span.
//
// Writes up to `cap` lines; returns how many there are in all.
enum { MOY_WRAP_WORDS = 0, MOY_WRAP_PANEL = 1 };
typedef struct {
    uint16_t off, len;
    uint8_t collapse;
} moy_text_line_t;
int moy_text_wrap(uint32_t font, const uint8_t *s, size_t n, int cols, int mode,
                  moy_text_line_t *lines, int cap);

// A collapsed line's text: its words joined by single spaces, into `out`
// (at least line->len bytes). Returns the byte count.
size_t moy_text_collapse(const uint8_t *s, const moy_text_line_t *line, uint8_t *out);

#endif // MOY_TEXT_H

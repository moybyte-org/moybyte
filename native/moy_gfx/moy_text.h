// moy_text: the one UI text path (docs/kernel_toolkit_2026-10.md §4).
//
// Every UI string reaches the raster through here: the system canvases'
// print (the print gate and the `text` kernel at every scale above 1), the
// scaled display type, the toolkit's widths and word wrap, moy_chrome's
// panels and a compiled app's labels. A cart's own `print` stays libmoy's
// `moy_print` at 8 px on every cart runtime (SPEC.md §6) and does not come
// here.
//
// TEXT IS UTF-8 BYTES, MEASURED IN BYTES: the font advances one cell per byte,
// so a width, a fit and a wrap count bytes, and a truncation NEVER cuts a
// UTF-8 sequence -- it backs up to the last whole codepoint. For ASCII a byte
// is a character; anything else draws as the font's first glyph, a cell per
// byte, until a font covers it.
//
// EVERY CALL CARRIES ITS SOURCE: TABLE (a string id's text), PY (text a
// runtime passed in), DATA (a number or a user's text formatted into a table
// entry) or LIT (a literal in C). LIT never reaches the raster: moy_text
// refuses it.
//
// THE FONT is a handle-shaped uint32. There is one, petme128, id 0: libmoy's
// compiled-in copy, byte-identical to runtime/font.py's (SPEC.md §6 requires
// it, and the spec's text scenes hold it).

#ifndef MOY_TEXT_H
#define MOY_TEXT_H

#include <stddef.h>
#include <stdint.h>

enum { MOY_TEXT_TABLE = 0, MOY_TEXT_PY = 1, MOY_TEXT_DATA = 2, MOY_TEXT_LIT = 3 };
enum { MOY_TEXT_LEFT = 0, MOY_TEXT_CENTER = 1, MOY_TEXT_RIGHT = 2 };

#define MOY_FONT_PETME128 0u
#define MOY_TEXT_CELL 8         // petme128's advance and height, in pixels

// Where text lands: an RGB565 buffer `dw` pixels wide holding `cap` pixels,
// the camera and the clip (buffer space, exclusive right and bottom).
typedef struct {
    uint16_t *px;
    size_t cap;
    int dw;
    int cam_x, cam_y;
    int cx0, cy0, cx1, cy1;
} moy_text_target_t;

// Draw `s` (n bytes) at (x, y) in `col` at `scale`. With maxw >= 0 the text
// fits the box: it truncates to what fits in maxw pixels, at a codepoint
// boundary, and `align` places it in the box. The drawn width, or -1 when
// refused (a LIT source, an unknown font).
int moy_text(const moy_text_target_t *t, uint32_t font, uint8_t source,
             const uint8_t *s, size_t n, int x, int y, uint16_t col, int scale,
             int align, int maxw);

// The width `s` draws at `scale`, in pixels (0 for an unknown font).
int moy_text_width(uint32_t font, const uint8_t *s, size_t n, int scale);

// The longest prefix of `s` that is at most `cells` bytes and ends on a
// codepoint boundary.
size_t moy_text_fit(const uint8_t *s, size_t n, size_t cells);

// Word wrap into lines of at most `cols` bytes.
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

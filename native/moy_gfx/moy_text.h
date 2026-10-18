// moy_text: the one UI text path (docs/kernel_toolkit_2026-10.md §4).
//
// Every UI string reaches the raster through here: the system canvases'
// print (the print gate and the `text` kernel at every scale above 1), the
// scaled display type, the toolkit's widths and word wrap, moy_chrome's
// panels and a compiled app's labels. A cart's own `print` stays libmoy's
// `moy_print` at 8 px on every cart runtime (SPEC.md §6) and does not come
// here.
//
// TEXT IS UTF-8 BYTES, cut only at codepoint boundaries: a wrap counts bytes
// and a truncation NEVER cuts a UTF-8 sequence -- it backs up to the last
// whole codepoint. How text meets glyphs is the font's: petme128 and a
// version 1 font draw one glyph per byte (petme128's are all one 8px cell),
// so anything past ASCII draws as the font's first glyph, a glyph a byte; a
// version 2 font decodes the UTF-8, and a codepoint draws at its byte:
// Latin-1 at its own value, MOY_FONT_BYTE_MAP's symbols at 0x80 up, any
// other codepoint (or a malformed sequence) at byte 0, the font's first
// record.

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

// The font file (docs/theming_2026-09.md §4.7 has both versions). Version 1:
// a 16-byte header, the advance table padded to 4 bytes, the glyphs in one
// box of at most 16x16 at 1 bpp. Version 2: a 16-byte header, the byte
// index, an 8-byte record a glyph (its own box, bearing and advance), the
// kerning pairs and the glyphs' coverage at 1, 2 or 4 bpp. Every field is
// read a byte at a time.
#define MOY_FONT_MAGIC "MFNT"
#define MOY_FONT_VERSION 2      // the newest version the reader takes
#define MOY_FONT_HEADER 16
#define MOY_FONT_CELL_MAX 16    // version 1: a glyph box's width and height, in pixels
#define MOY_FONT_LINE_MAX 32    // version 1: the line advance and an advance
#define MOY_FONT_INDEX 256      // version 2: the byte index, a record a byte
#define MOY_FONT_RECORD 8       // version 2: offset (3), w, h, x, y, advance
#define MOY_FONT_PAIR 3         // version 2: left byte, right byte, dx
#define MOY_FONT_BYTES 65536    // the largest file the reader takes
#define MOY_FONT_SLOTS 16       // loaded fonts at once, beside petme128

// The byte map: the codepoints a version 2 font draws at bytes 0x80 up, in
// order (tools/make_font.py reads this line).
#define MOY_FONT_BYTE_MAP {0x2212, 0x2190, 0x2192, 0x2191, 0x2193, 0x2026, 0x2022, \
                           0x201C, 0x201D, 0x2018, 0x2019, 0x2013, 0x2014}
#define MOY_FONT_ELLIPSIS 0x85  // U+2026's byte: a version 2 cut ends in it

// What moy_font_load answers: OK, or why the file was refused.
enum {
    MOY_FONT_OK = 0,
    MOY_FONT_BIG = 1,           // over MOY_FONT_BYTES
    MOY_FONT_SHORT = 2,         // under its header, or not the length its header says
    MOY_FONT_MAGIC_BAD = 3,     // not a font file
    MOY_FONT_NEWER = 4,         // a version this reader does not take
    MOY_FONT_BAD = 5,           // a field out of its range, a reserved, padding or pad bit set, tables that do not fill the file
    MOY_FONT_FULL = 6,          // every slot holds a font
    MOY_FONT_NOMEM = 7,
};

// A font's metrics. Version 1 and petme128: its glyph box, its line advance,
// its baseline (rows from the box's top) and the bytes its glyphs cover.
// Version 2: `w` its cell (the advance a gap counted in cells takes), `h`
// the rows from the line's top to its deepest ink, the line, the baseline
// (rows from the line's top), `first` 0 and `count` its records. `bpp` is
// its coverage's bits a pixel, `version` its file's (0 for petme128).
typedef struct {
    int w, h, line, ascent, first, count;
    int bpp, version;
} moy_font_info_t;

// Check font file `src` (n bytes, at any address) and load it into a row of
// its own. MOY_FONT_OK with *id set, or a refusal with nothing loaded.
int moy_font_load(const uint8_t *src, size_t n, uint32_t *id);

// Free a loaded font's row; its id is refused from then on. 1, or 0 for an id
// that names no loaded font (petme128 is never freed).
int moy_font_free(uint32_t font);

// Free every loaded font. The slots are the process's and outlive a VM.
void moy_font_reset(void);
// The kernel's stop hook (native/moy_kernel): every font goes with the VM
// that loaded it.
void moy_text_vm_stop(void);

// Font `font`'s metrics into *out: 1, or 0 for an unknown font.
int moy_font_info(uint32_t font, moy_font_info_t *out);

// THE PRINT FONT: the font a canvas's print draws in (the print gate and the
// `text` op; docs/theming_2026-09.md §4.7). Petme128 until the shell names
// one, and again while a cart's code runs and once the font is freed; the
// Lua C API and libmoy's moy_print never read it. moy_text_print_font_use
// installs `font` (0 for petme128) and answers the one it replaced, or
// installs nothing and answers -1 for a font that is not loaded.
uint32_t moy_text_print_font(void);
int64_t moy_text_print_font_use(uint32_t font);

// The byte order of every target's words, which the glyph lane blends in:
// byte-swapped RGB565 (1, the default: the S3 panels, the host and the
// browser) or canonical (0: the P4's DPI scan-out). The canvas sets it from
// its wire palette (device_canvas.WIRE_SWAPPED).
void moy_text_wire(int swapped);
int moy_text_wire_swapped(void);

// The version 2 glyphs drawn since boot: out[0] those that read nothing (from
// a ramp, or 1 bpp opaque ink), out[1] those that read the target (the glyph
// lane's op count, docs/look_2026-10.md step 4).
void moy_text_lanes(uint32_t out[2]);

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

// Draw `s` (n bytes) at (x, y), the line's top left, in `col` at `scale`.
// With maxw >= 0 the text fits the box: it truncates to what fits in maxw
// pixels, at a codepoint boundary (in a version 2 font that covers U+2026,
// to what fits with "\xe2\x80\xa6" after it: moy_text_cut_px), and `align`
// places it in the box. The drawn width, or -1 when refused (a LIT source,
// an unknown font).
int moy_text(const moy_text_target_t *t, uint32_t font, uint8_t source,
             const uint8_t *s, size_t n, int x, int y, uint16_t col, int scale,
             int align, int maxw);

// How the text meets what is under it (docs/look_2026-10.md step 4). A
// version 2 font's 2 and 4 bpp coverage blends its ink into the target: by
// default it reads each partly covered pixel and blends over it. With `bg`
// the caller holds that every pixel of the box (bx, by, bw, bh, in the
// text's coordinates: the camera applies) is that RGB565 colour, and a glyph
// that lies inside the box and over no glyph drawn before it stores from a
// ramp built once a pass and reads nothing; every other glyph reads. With a
// `shadow` colour the whole string draws first at (shadow_dx, shadow_dy)
// unscaled pixels from the text (the scale applies) in that colour at
// `shadow_a` (1..255) of its coverage, and the text over it reads. Petme128
// and version 1 fonts store a whole pixel: their shadow is solid.
#define MOY_TEXT_BG_READ (-1)       // bg: no known background
#define MOY_TEXT_NO_SHADOW (-1)     // shadow: none
typedef struct {
    int32_t bg;
    int bx, by, bw, bh;
    int32_t shadow;
    int shadow_a, shadow_dx, shadow_dy;
} moy_text_paint_t;

// moy_text with a paint; NULL is moy_text's: no known background, no shadow.
int moy_text_paint(const moy_text_target_t *t, uint32_t font, uint8_t source,
                   const uint8_t *s, size_t n, int x, int y, uint16_t col, int scale,
                   int align, int maxw, const moy_text_paint_t *p);

// The width `s` draws at `scale`, in pixels (0 for an unknown font): the sum
// of its glyphs' advances and, in a version 2 font, its kerning pairs'.
int moy_text_width(uint32_t font, const uint8_t *s, size_t n, int scale);

// The columns `s`'s ink covers at `scale`, from its pen's start, in *x0 up to
// *x1 (exclusive): a version 2 font's glyph boxes; petme128's and a version
// 1 font's, their cells (the advance). Answers 0, and the advance, where `s`
// draws no ink or `font` is unknown. A part centres a label by its ink.
int moy_text_ink(uint32_t font, const uint8_t *s, size_t n, int scale, int *x0, int *x1);

// 1 when `font` draws every codepoint of `s` with a glyph of its own (a
// stand-in the font maps counts), 0 where one falls to the font's first
// record or `font` is unknown. Petme128 and a version 1 font draw printable
// ASCII; a version 2 font each byte its index gives a record past the
// first, and the space.
int moy_text_covers(uint32_t font, const uint8_t *s, size_t n);

// The longest prefix of `s` that is at most `cells` bytes and ends on a
// codepoint boundary.
size_t moy_text_fit(const uint8_t *s, size_t n, size_t cells);

// The longest prefix of `s` that draws in at most `maxw` pixels at `scale`
// in `font` and ends on a codepoint boundary; 0 for an unknown font or a
// negative width.
size_t moy_text_fit_px(uint32_t font, const uint8_t *s, size_t n, int maxw, int scale);

// A cut with an ellipsis: what moy_text draws for a box `maxw` wide. Where `s`
// fits, all of it and *ellipsis 0. Where it does not and `font` is a version 2
// font that covers U+2026, the longest prefix that draws in maxw with the
// ellipsis after it, its trailing spaces dropped, and *ellipsis 1. Otherwise
// moy_text_fit_px's prefix and *ellipsis 0.
size_t moy_text_cut_px(uint32_t font, const uint8_t *s, size_t n, int maxw, int scale,
                       int *ellipsis);

// Word wrap into lines of at most `cols` bytes (or pixels), for any known font.
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
// MOY_WRAP_WORDS_PX: MOY_WRAP_WORDS with `cols` a line's width in pixels of
// the font's advances at scale 1 (a space's advance joins two words).
//
// Writes up to `cap` lines; returns how many there are in all.
enum { MOY_WRAP_WORDS = 0, MOY_WRAP_PANEL = 1, MOY_WRAP_WORDS_PX = 2 };
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

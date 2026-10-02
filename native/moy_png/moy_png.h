// moy_png: the reader for a cart's cover, `cover.png` (SPEC.md 3.6).
//
// THE PROFILE, and nothing else: a PNG of exactly 128 x 128 pixels, bit depth
// 8, colour type 3 (indexed, PLTE of 1-256 entries) or 2 (RGB), not
// interlaced, no tRNS, at most 65,536 bytes. All five row filters; ancillary
// chunks skipped; chunk CRCs not checked. A file outside the profile, or one
// whose data does not decode, is not a cover -- the caller draws as if there
// were none. moy-spec's moycore/cover.py `read` is the reference this mirrors
// rule for rule, and runtime/cover_png.py is the console's Python twin of it;
// tests/test_cover_png.py holds the three together.
//
// PURE C, no MicroPython: modmoy_png.c is the binding the boards and the
// browser compile, and the host test lane builds this file on its own.
//
// STREAMED, in fixed memory: the caller hands over ONE work buffer of
// moy_png_work_size() bytes -- the inflate window, two raw rows and the
// decoder's state -- and the zlib stream is inflated a row at a time into it,
// never whole. Nothing here allocates, and the work buffer holds offsets, not
// pointers, so a decode may stop after any row and resume on a later call
// with the same buffers.
//
// THE OUTPUT is (128 / div)^2 pixels: div 1 is the picture itself; a larger
// power of two is its box-filtered reduction (each output pixel the rounded
// mean of a div x div block). In one of four formats: R, G, B bytes; RGB565
// little-endian; RGB565 high byte first (the S3 panels' wire order); or an
// index into a caller's palette (the nearest entry by squared RGB distance,
// the lowest index on a tie).
//
// The zlib stream is held to what zlib holds it to: a stream that zlib would
// refuse -- a bad header, a bad block, a distance before the start, a wrong
// Adler-32, data past the picture, a stream that does not end -- is refused
// here too, so the C and the Python twin agree on every file.

#ifndef MOY_PNG_H
#define MOY_PNG_H

#include <stddef.h>
#include <stdint.h>

#define MOY_PNG_SIDE      128
#define MOY_PNG_MAX_BYTES 65536

// Output formats.
#define MOY_PNG_RGB888    0
#define MOY_PNG_RGB565    1
#define MOY_PNG_RGB565_SW 2
#define MOY_PNG_INDEX     3

// Bytes the work buffer must hold (it must also be 4-byte aligned): the 32 KB
// inflate window, two raw rows of 1 + 128 * 3 bytes, and the decoder's state.
// A constant, so every caller allocates the same one buffer once.
#define MOY_PNG_WORK 38912

size_t moy_png_work_size(void);

// Bytes the output buffer must hold for (div, fmt), or 0 when either is bad.
size_t moy_png_out_size(int div, int fmt);

// Read the file's structure and set up a decode. 1: in the profile, decode
// with moy_png_rows. 0: not a cover. -1: a bad argument (work too small or
// misaligned, div not a power of two dividing 128, fmt unknown, a mapping
// palette missing or over 256 entries).
//
// `pal`/`npal` is the palette MOY_PNG_INDEX maps to (npal entries of R, G, B);
// the other formats ignore it.
int moy_png_begin(void *work, size_t work_len, const uint8_t *data, size_t len,
                  int div, int fmt, const uint8_t *pal, int npal);

// Decode up to `n` more OUTPUT rows into `out`. 1: the whole picture is in
// `out` and the file is a cover. 0: more rows remain. -1: the file is not a
// cover after all (and every later call answers -1). `data`/`len` must be the
// bytes moy_png_begin read.
int moy_png_rows(void *work, const uint8_t *data, size_t len, uint8_t *out,
                 size_t out_len, int n);

#endif

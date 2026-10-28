// moy_img: the moyimg-v1 decoder in C (docs/kernel_cartpath_2026-10.md §1),
// the reader a cart's `images/*.moyimg` crosses through when its run has no
// VM. runtime/moyimg.py's `decode_moyimg` is the reference, rule for rule, and
// tests/test_moy_img.py holds the two together over every seed image.
//
// A `.moyimg` is a JSON object {format, w, h, data}: `w` and `h` as Python's
// int() makes them, both above zero, and `data` the base64 of a ZLIB stream
// of exactly w*h palette indices, one byte a pixel. Anything else is not a
// picture (MOY_IMG_NOT), which every caller draws as an absent one.
//
// PURE C, no MicroPython, and no allocation of its own: the base64 is decoded
// as the inflater asks for each byte, straight into the caller's buffer, which
// is also the inflater's window. `work` is the inflater's state, the caller's
// to place (MOY_IMG_WORK bytes; the stack of the task that decodes is fine).

#ifndef MOY_IMG_H
#define MOY_IMG_H

#include <stddef.h>
#include <stdint.h>

enum {
    MOY_IMG_OK = 0,
    MOY_IMG_NOT = -1,       // not a picture: a corrupt or foreign blob
    MOY_IMG_ROOM = -2,      // the caller's buffer holds fewer than w*h bytes
    MOY_IMG_READ = -3,      // the streamed reader's source failed
};

// The inflater's state, in bytes: what `work` must hold.
size_t moy_img_work_size(void);

// The picture's size without its pixels: MOY_IMG_OK, or MOY_IMG_NOT.
int moy_img_head(const char *text, size_t n, uint32_t *w, uint32_t *h);

// The picture's w*h indices into `pix` (`cap` bytes, at least w*h + 1: the
// byte past the picture is how a stream longer than it is seen): MOY_IMG_OK
// with *w and *h set, MOY_IMG_NOT, or MOY_IMG_ROOM. `work` holds
// moy_img_work_size() bytes, aligned for a pointer.
int moy_img_decode(const char *text, size_t n, uint8_t *pix, size_t cap,
                   uint32_t *w, uint32_t *h, void *work);

// -- the streamed reader ----------------------------------------------------------
//
// A picture read from its FILE, a row at a time, and resampled into a screen
// as it comes: no w*h raster anywhere and no copy of the file. It reads the
// form the writers write -- `data` after `w` and `h` and near the end, its
// base64 bare (no escape, no whitespace), padded -- and nothing else:
//
//   the head    the bytes up to and including the first `"data"`, which ends
//               within the first MOY_IMG_HEAD bytes. Cut back past its
//               whitespace and one comma and closed with `}`, it is a JSON
//               object whose `w` and `h` read as moy_img_head reads them.
//   the value   whitespace, `:`, whitespace, `"`, base64 characters, at most
//               two `=` (exactly the padding the count asks for, which is
//               never 4k+1), `"`.
//   the rest    at most MOY_IMG_HEAD bytes: whitespace, `}`, whitespace, the
//               end; or whitespace, `,` and members that, opened with `{` in
//               the comma's place, are a JSON object with at least one member
//               (a copy's provenance).
//   the stream  a ZLIB stream of exactly w*h indices, its checksum good; what
//               follows its end inside the base64 is not read as pixels.
//
// runtime/moyimg.py's `cover_stream` is the reference, rule for rule.

#define MOY_IMG_HEAD 256u

// Up to `n` bytes of the source into `buf`: how many, 0 at its end, < 0 when
// it failed.
typedef int (*moy_img_read_t)(void *ctx, uint8_t *buf, size_t n);

// The streamed reader's state, in bytes, the inflater's 32 KiB window in it:
// the caller places it (PSRAM on a board), aligned for a pointer.
size_t moy_img_stream_size(void);

// Begin reading the source: MOY_IMG_OK with the picture's size, MOY_IMG_NOT,
// or MOY_IMG_READ.
int moy_img_stream_head(void *st, moy_img_read_t rd, void *ctx, uint32_t *w, uint32_t *h);

// The rest of the picture, COVER-sampled into `dst` (dw*dh indices): the
// source cropped about its centre to dst's aspect and sampled nearest, the
// formula runtime/file_widgets.py's `cover_indices` is. `row` holds w bytes.
// MOY_IMG_OK, MOY_IMG_NOT (dst then holds part of a picture) or MOY_IMG_READ.
int moy_img_stream_cover(void *st, uint8_t *row, uint8_t *dst, uint32_t dw, uint32_t dh);

#endif // MOY_IMG_H

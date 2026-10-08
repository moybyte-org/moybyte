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

#endif // MOY_IMG_H

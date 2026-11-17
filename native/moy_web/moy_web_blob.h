/* The browser console this image names.
 *
 * The console is not in the image. It is the add-on cart's `moybyte_web.pack`
 * (native/moy_net/moy_page.h, THE PACK), and the image carries only the
 * sha256 of the pack's body it serves (moy_web_want) and the stamp of the
 * bundle that pack was made from. Both are GENERATED (moy_web_blob.gen.c, by
 * tools/gen_web_blob.py, which writes the pack too); this header is the
 * contract between that file and its readers, and the only part a human
 * edits. */
#ifndef MOY_WEB_BLOB_H
#define MOY_WEB_BLOB_H

#include <stddef.h>
#include <stdint.h>

/* 1 when this image names a page (a bundle was built with it), else 0. */
extern const unsigned char moy_web_named;
extern const unsigned char moy_web_want[32];
/* "<count> <total bytes> <12 hex of the bundle digest>", or "0 0 none". */
extern const char moy_web_stamp[];

#endif /* MOY_WEB_BLOB_H */

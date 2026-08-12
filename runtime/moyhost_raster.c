/* The host's libmoy RASTER shim (moycore plan rung 5).
 *
 * `runtime/canvas.py` is a pure-Python transcription of the same raster
 * libmoy implements in C, kept in agreement with it by conformance goldens.
 * This is how that C reaches CPython instead: one shared library holding
 * libmoy's canvas/sprite/data translation units plus the handful of entry
 * points below, loaded with ctypes by runtime/raster_binding.py -- exactly the
 * shape runtime/moyhost_audio.c already uses for the synth (stage 0).
 *
 * BUILT INDEXED, and that is what makes the swap free rather than delicate:
 * without MOY_PIXEL_RGB565 a libmoy pixel is one byte holding a palette index,
 * which is byte-for-byte the buffer `Canvas.buf` already is. The library draws
 * straight into the bytearray Python owns -- no conversion, no copy, and
 * to_rgb888/blit_indices/the editors keep reading exactly what they read
 * before. (The boards build the same sources with the define set; that
 * difference is a build option, not a fork.)
 *
 * Everything here is a thin forward. The one thing it adds is OWNERSHIP: a
 * `host_raster` bundles the canvas with the sheet and map a scene draws from,
 * so ctypes hands around one opaque pointer instead of three structs whose
 * layout Python would then have to know -- which is how a binding starts
 * duplicating a header.
 */

#include <stdlib.h>
#include <string.h>

#include "moy.h"

typedef struct {
    moy_canvas c;
    moy_sheet  s;
    moy_map    m;
    int        has_sheet, has_map;
} host_raster;

host_raster *hr_new(uint8_t *pix, int w, int h)
{
    host_raster *r = (host_raster *)calloc(1, sizeof(host_raster));
    if (!r) return NULL;
    moy_canvas_init(&r->c, pix, w, h);
    return r;
}

void hr_free(host_raster *r) { free(r); }

/* The buffer can move under us: Python owns it, and a resize makes a new
 * bytearray. Re-point rather than rebuild, so draw state survives. */
void hr_retarget(host_raster *r, uint8_t *pix, int w, int h)
{
    r->c.pix = pix;
    r->c.w = w;
    r->c.h = h;
}

void hr_set_sheet(host_raster *r, uint8_t *pix)
{
    if (pix) { moy_sheet_init(&r->s, pix); r->has_sheet = 1; }
    else r->has_sheet = 0;
}

void hr_set_map(host_raster *r, uint8_t *cells, int w, int h)
{
    if (cells && w > 0 && h > 0) { moy_map_init(&r->m, cells, w, h); r->has_map = 1; }
    else r->has_map = 0;
}

void hr_reset_state(host_raster *r) { moy_reset_state(&r->c); }

void hr_cls  (host_raster *r, int col) { moy_cls(&r->c, col); }
void hr_pix  (host_raster *r, int x, int y, int col) { moy_pix(&r->c, x, y, col); }
void hr_line (host_raster *r, int x0, int y0, int x1, int y1, int col) { moy_line(&r->c, x0, y0, x1, y1, col); }
void hr_rect (host_raster *r, int x, int y, int w, int h, int col) { moy_rect(&r->c, x, y, w, h, col); }
void hr_rectb(host_raster *r, int x, int y, int w, int h, int col) { moy_rectb(&r->c, x, y, w, h, col); }
void hr_circ (host_raster *r, int cx, int cy, int rad, int col) { moy_circ(&r->c, cx, cy, rad, col); }
void hr_circb(host_raster *r, int cx, int cy, int rad, int col) { moy_circb(&r->c, cx, cy, rad, col); }
void hr_tri  (host_raster *r, int x1, int y1, int x2, int y2, int x3, int y3, int col) { moy_tri(&r->c, x1, y1, x2, y2, x3, y3, col); }
void hr_trib (host_raster *r, int x1, int y1, int x2, int y2, int x3, int y3, int col) { moy_trib(&r->c, x1, y1, x2, y2, x3, y3, col); }

/* print walks BYTES (SPEC.md 6: one 8px cell per byte), so the length comes
 * from Python rather than from a NUL -- the text_bytes scene prints 0xFF. */
void hr_print(host_raster *r, const uint8_t *s, int len, int x, int y, int col)
{
    moy_print(&r->c, s, (size_t)len, x, y, col);
}

void hr_camera(host_raster *r, int x, int y) { moy_camera(&r->c, x, y); }
void hr_camera_reset(host_raster *r) { moy_camera_reset(&r->c); }
void hr_clip(host_raster *r, int x, int y, int w, int h) { moy_clip(&r->c, x, y, w, h); }
void hr_clip_reset(host_raster *r) { moy_clip_reset(&r->c); }
void hr_pal(host_raster *r, int c0, int c1) { moy_pal(&r->c, c0, c1); }
void hr_pal_reset(host_raster *r) { moy_pal_reset(&r->c); }
void hr_palt(host_raster *r, int col, int on) { moy_palt(&r->c, col, on); }
void hr_palt_reset(host_raster *r) { moy_palt_reset(&r->c); }

void hr_spr(host_raster *r, int n, int x, int y, int ck, int scale, int flip)
{
    if (r->has_sheet) moy_spr(&r->c, &r->s, n, x, y, ck, scale, flip);
}

void hr_sspr(host_raster *r, int sx, int sy, int sw, int sh,
             int dx, int dy, int dw, int dh, int ck, int flip)
{
    if (r->has_sheet) moy_sspr(&r->c, &r->s, sx, sy, sw, sh, dx, dy, dw, dh, ck, flip);
}

void hr_map(host_raster *r, int mx, int my, int w, int h,
            int sx, int sy, int ck, int scale)
{
    if (r->has_sheet && r->has_map)
        moy_map_draw(&r->c, &r->m, &r->s, mx, my, w, h, sx, sy, ck, scale);
}

void hr_tline(host_raster *r, int x0, int y0, int x1, int y1,
              int u, int v, int du, int dv, int ck)
{
    if (r->has_sheet && r->has_map)
        moy_tline(&r->c, &r->s, &r->m, x0, y0, x1, y1, u, v, du, dv, ck);
}

int  hr_mget(host_raster *r, int x, int y) { return r->has_map ? moy_mget(&r->m, x, y) : 0; }
void hr_mset(host_raster *r, int x, int y, int tile) { if (r->has_map) moy_mset(&r->m, x, y, tile); }

/* Read a pixel back as an INDEX. Trivial on this build -- the buffer already
 * holds indices -- but it belongs here rather than in Python so the bounds and
 * the camera offset follow the same rules the writes do. */
int hr_peek(host_raster *r, int x, int y)
{
    x -= r->c.cam_x;
    y -= r->c.cam_y;
    if (x < 0 || y < 0 || x >= r->c.w || y >= r->c.h) return 0;
    return r->c.pix[(size_t)y * (size_t)r->c.w + (size_t)x];
}

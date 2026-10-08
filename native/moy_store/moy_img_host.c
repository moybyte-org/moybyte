// moy_img on the host, by ctypes (runtime/moy_play.py's image_decode): one
// decode with its work on the stack, for the parity test against
// runtime/moyimg.py.

#include "moy_img.h"

int moy_img_host_decode(const char *text, size_t n, uint8_t *pix, size_t cap,
                        uint32_t *w, uint32_t *h) {
    _Alignas(8) unsigned char work[2048];
    if (moy_img_work_size() > sizeof(work)) {
        return -9;
    }
    return moy_img_decode(text, n, pix, cap, w, h, work);
}

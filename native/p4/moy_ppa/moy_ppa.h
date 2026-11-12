// moy_ppa: the PPA's C entries the kernel's compositors take (modmoy_ppa.c).

#ifndef MOY_PPA_H
#define MOY_PPA_H

#include <stddef.h>
#include <stdint.h>

int moy_ppa_k_scale(uint16_t *dst, int dw, int dh, int dx, int dy, const uint16_t *src, int sw,
                    int sh, int scale);
int moy_ppa_k_scale_nb(uint16_t *dst, int dw, int dh, int dx, int dy, const void *src, int sw,
                       int sh, int scale);
long moy_ppa_k_snap(uint8_t *scr, size_t len, const uint8_t *frame, size_t n);
void moy_ppa_k_sync(void);
void moy_ppa_k_snap_wait(void);

#endif // MOY_PPA_H

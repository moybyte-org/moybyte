// The raster's wire order (moy_gfx_base.h).

#include "moy_gfx_base.h"

int moy_gfx_swapped = 1;

int moy_gfx_wire(int swapped) {
    int was = moy_gfx_swapped;
    if (swapped >= 0) {
        moy_gfx_swapped = swapped != 0;
    }
    return was;
}

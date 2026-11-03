// moy_light: the panel light's level as the kernel speaks it -- 0 off, 255
// full (MOY_KERNEL_PANEL(backlight)) -- and the panel modules' Python verb's
// argument. Each board's module owns how a level reaches its light.

#ifndef MOY_LIGHT_H
#define MOY_LIGHT_H

#include "py/obj.h"

#define MOY_LIGHT_FULL 255

// backlight(x)'s x as a level: True full, False off, an int held in 0..255.
static inline int moy_light_arg(mp_obj_t x) {
    if (mp_obj_is_bool(x)) {
        return x == mp_const_true ? MOY_LIGHT_FULL : 0;
    }
    mp_int_t v = mp_obj_get_int(x);
    return v < 0 ? 0 : v > MOY_LIGHT_FULL ? MOY_LIGHT_FULL : (int)v;
}

#endif // MOY_LIGHT_H

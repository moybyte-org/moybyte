// moy_c6 -- a P4's ESP-NOW over ESP-Hosted (modmoy_c6.c): what the rest of
// the kernel may ask it.
#ifndef MOYBYTE_MOY_C6_H
#define MOYBYTE_MOY_C6_H

#include <stdint.h>

// The longest ESP-NOW frame the C6's slave carries: 1470 from shim 4 (v2
// frames), otherwise 250. native/moy_net/moy_link.c reaches it weakly.
uint32_t moy_c6_frame_max(void);

#endif

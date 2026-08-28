#ifndef H_STUB_PY_MPHAL_H
#define H_STUB_PY_MPHAL_H

#include <stdint.h>

// A real mp_hal_delay_ms yields the core. So does this one -- which is what
// makes moy_flush_stop()'s 1ms-at-a-time wait a wait the feeder can finish
// inside, instead of a spin nothing else runs during.
void mp_hal_delay_ms(uint32_t ms);

#endif // H_STUB_PY_MPHAL_H

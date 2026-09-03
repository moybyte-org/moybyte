#ifndef H_STUB_ESP_TIMER_H
#define H_STUB_ESP_TIMER_H

#include <stdint.h>

#include "harness.h"

// The virtual clock. int64_t like the real one, so the engine's deliberate
// LOW-32-BIT stamps (done_us, kick_us) truncate exactly as they do on a board
// -- which is what the wrap scenario exercises.
static inline int64_t esp_timer_get_time(void) { return h_now(); }

#endif // H_STUB_ESP_TIMER_H

// The FreeRTOS surface moy_flush uses, and nothing else.
#ifndef H_STUB_FREERTOS_H
#define H_STUB_FREERTOS_H

#include <stdint.h>

#include "harness.h"

typedef int BaseType_t;
typedef unsigned int UBaseType_t;
typedef uint32_t TickType_t;

#define pdTRUE  ((BaseType_t)1)
#define pdFALSE ((BaseType_t)0)
#define pdPASS  pdTRUE
#define pdFAIL  pdFALSE

#define portMAX_DELAY ((TickType_t)0xFFFFFFFFU)

// The boards' CONFIG_FREERTOS_HZ. Kept at 100 on purpose: it is what makes
// pdMS_TO_TICKS(5) evaluate to ZERO ticks, the busy-spin the feed loop's
// "2 ticks, NOT pdMS_TO_TICKS(a small ms)" comment is about.
#define configTICK_RATE_HZ 100
#define pdMS_TO_TICKS(ms) \
    ((TickType_t)(((uint32_t)(ms) * (uint32_t)configTICK_RATE_HZ) / 1000U))

// The ARG form, as IDF spells it -- so moy_flush.h's traceISR_EXIT_TO_SCHEDULER
// fallback (the guard both panel modules used to carry) is actually expanded
// here rather than merely compiled past.
#define portYIELD_FROM_ISR(x)                   \
    do {                                        \
        if (x) {                                \
            traceISR_EXIT_TO_SCHEDULER();       \
            h_isr_yield_requested();            \
        }                                       \
    } while (0)

void h_isr_yield_requested(void);

#endif // H_STUB_FREERTOS_H
